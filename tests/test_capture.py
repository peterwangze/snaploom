"""Check screen-coordinate mapping, plus an opt-in native desktop smoke test."""

from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from snaploom.capture import ScreenFrame, Snapshot, _composite, capture_desktop


def _solid_frame(rect: QRect, size: QSize, color: str) -> ScreenFrame:
    image = QImage(size, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    return ScreenFrame(rect, image)


class ScreenCoordinateTests(unittest.TestCase):
    def setUp(self):
        self.bounds = QRect(-100, 0, 200, 100)
        frames = (
            _solid_frame(QRect(-100, 0, 100, 100), QSize(100, 100), "red"),
            _solid_frame(QRect(0, 0, 100, 100), QSize(200, 200), "blue"),
        )
        self.snapshot = Snapshot(_composite(frames, self.bounds, 1), self.bounds, frames)

    def test_single_monitor_crop_uses_its_native_pixels(self):
        left, right = QRect(-30, 10, 20, 10), QRect(10, 10, 20, 10)
        self.assertEqual(self.snapshot.pixel_size(left), QSize(20, 10))
        self.assertEqual(self.snapshot.pixel_size(right), QSize(40, 20))
        self.assertEqual(self.snapshot.crop(right).size(), QSize(40, 20))
        self.assertEqual(self.snapshot.color_at(QPoint(-30, 10)), QColor("red"))
        self.assertEqual(self.snapshot.color_at(QPoint(10, 10)), QColor("blue"))

    def test_cross_screen_crop_uses_one_consistent_scale(self):
        rect = QRect(-20, 10, 40, 20)
        cropped = self.snapshot.crop(rect)
        self.assertEqual(self.snapshot.pixel_size(rect), cropped.size())
        self.assertEqual(cropped.size(), QSize(80, 40))
        self.assertEqual(cropped.pixelColor(39, 0), QColor("red"))
        self.assertEqual(cropped.pixelColor(40, 0), QColor("blue"))

    def test_outside_desktop_crop_is_empty(self):
        rect = QRect(150, 20, 40, 40)
        self.assertTrue(self.snapshot.crop(rect).isNull())
        self.assertTrue(self.snapshot.pixel_size(rect).isEmpty())


@unittest.skipUnless(os.environ.get("SNAPLOOM_TEST_DESKTOP") == "1", "needs a live test desktop")
class NativeDesktopTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "requires Windows desktop composition")
    def test_region_button_captures_the_background_without_dashboard_ghosting(self):
        from snaploom.app import Application

        qt = QApplication.instance() or QApplication([])
        available = qt.primaryScreen().availableGeometry()
        background = QWidget(None, Qt.WindowType.FramelessWindowHint)
        background.setGeometry(available.adjusted(12, 12, -12, -12))
        background.setStyleSheet("background: #254a6b")
        with TemporaryDirectory() as directory:
            app = Application(qt, Path(directory), testing=True)
            dashboard = app.dashboard
            dashboard.move(available.center() - QPoint(dashboard.width() // 2, dashboard.height() // 2))
            button = next(button for button in dashboard.findChildren(QPushButton)
                          if button.text() in {"Capture region", "区域截图"})
            try:
                background.show()
                background.raise_()
                self.assertTrue(QTest.qWaitForWindowExposed(background, 2000))
                probe = QRect(available.center() - QPoint(120, 80), QSize(240, 160))
                expected = QColor("#254a6b")
                for _ in range(10):
                    QTest.qWait(30)
                    if capture_desktop().color_at(probe.center()) == expected:
                        break
                clean = capture_desktop().crop(probe)
                self.assertEqual(clean.pixelColor(clean.rect().center()), expected)

                for _ in range(3):
                    app.show_dashboard()
                    self.assertTrue(QTest.qWaitForWindowExposed(dashboard, 2000))
                    QTest.qWait(250)
                    self.assertNotEqual(capture_desktop().crop(probe), clean)
                    window = dashboard.windowHandle()
                    QTest.mouseClick(window, Qt.MouseButton.LeftButton,
                                     pos=dashboard.mapFromGlobal(button.mapToGlobal(button.rect().center())))
                    self.assertFalse(dashboard.isVisible())
                    for _ in range(80):
                        QTest.qWait(25)
                        if app.editor is not None:
                            break
                    self.assertIsNotNone(app.editor)
                    editor = app.editor
                    self.assertEqual(editor.snapshot.crop(probe), clean, "Dashboard pixels remained in the capture")
                    self.assertTrue(QTest.qWaitForWindowExposed(editor, 2000))
                    window = editor.windowHandle()
                    start = editor.mapFromGlobal(probe.topLeft() + QPoint(22, 22))
                    end = editor.mapFromGlobal(probe.bottomRight() - QPoint(22, 22))
                    QTest.mousePress(window, Qt.MouseButton.LeftButton, pos=start)
                    QTest.mouseMove(window, end)
                    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, pos=end)
                    self.assertTrue(editor.toolbar.isVisible())
                    initial = editor.snapshot
                    QTest.keyClick(window, Qt.Key.Key_F5)
                    self.assertFalse(editor.isVisible())
                    for _ in range(80):
                        QTest.qWait(25)
                        if editor.isVisible():
                            break
                    self.assertTrue(editor.isVisible())
                    self.assertIsNot(editor.snapshot, initial)
                    self.assertEqual(editor.snapshot.crop(probe), clean, "Editor pixels remained after refreshing")
                    QTest.keyClick(window, Qt.Key.Key_Escape)
                    self.assertIsNone(app.editor)
                    self.assertTrue(dashboard.isVisible())
            finally:
                app.release_capture(restore_dashboard=False)
                dashboard.hide()
                background.close()
                app.shutdown()

    def test_capture_reads_a_known_desktop_color_and_native_dimensions(self):
        app = QApplication.instance() or QApplication([])
        screen = app.primaryScreen()
        window = QWidget(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        palette = window.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#e32191"))
        window.setPalette(palette)
        window.setAutoFillBackground(True)
        bounds = QRect(QPoint(), QSize(160, 100))
        bounds.moveCenter(screen.availableGeometry().center())
        window.setGeometry(bounds)
        window.show()
        window.raise_()
        try:
            self.assertTrue(QTest.qWaitForWindowExposed(window, 2000))
            point = window.geometry().center()
            expected = QColor("#e32191")
            for _ in range(8):
                QTest.qWait(25)
                snapshot = capture_desktop()
                if snapshot.color_at(point) == expected:
                    break
            self.assertEqual(snapshot.color_at(point), expected)
            rect = QRect(point - QPoint(5, 5), QSize(10, 10))
            cropped = snapshot.crop(rect)
            self.assertEqual(snapshot.pixel_size(rect), cropped.size())
            self.assertEqual(cropped.pixelColor(0, 0), expected)
            self.assertEqual(snapshot.image.pixelColor(point - snapshot.geometry.topLeft()).alpha(), 255)
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
