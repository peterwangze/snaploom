"""Read back the actual desktop surface after a selection shrinks."""

import os
import unittest

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QRect, QSize, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from snaploom.capture import ScreenFrame, Snapshot
from snaploom.editor import CaptureEditor


@unittest.skipUnless(os.environ.get("SNAPLOOM_TEST_DESKTOP") == "1", "needs a live test desktop")
class DesktopSelectionTests(unittest.TestCase):
    def test_shrinking_selection_restores_pixels_on_the_desktop_surface(self):
        app = QApplication.instance() or QApplication([])
        screen = app.primaryScreen()
        size = QSize(520, 340)
        geometry = QRect(QPoint(), size)
        geometry.moveCenter(screen.availableGeometry().center())
        ratio = screen.devicePixelRatio()
        pixels = QImage(size * ratio, QImage.Format.Format_RGB32)
        dark, light = QColor("#24354c"), QColor("#f2e5ca")
        pixels.fill(dark)
        painter = QPainter(pixels)
        painter.fillRect(pixels.width() // 2, 0, pixels.width(), pixels.height(), light)
        painter.end()
        preview = pixels.scaled(size)
        editor = CaptureEditor(Snapshot(preview, geometry, (ScreenFrame(geometry, pixels),)))

        def mouse(kind, point, button=Qt.MouseButton.NoButton):
            event = QMouseEvent(kind, QPointF(point), QPointF(editor.mapToGlobal(point)), button,
                                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            QCoreApplication.sendEvent(editor, event)
            QCoreApplication.sendPostedEvents(None, QEvent.Type.UpdateRequest)

        def screen_pixels(ready):
            for _ in range(8):
                QTest.qWait(60)
                result = screen.grabWindow(int(editor.winId())).toImage()
                if not result.isNull() and ready(result):
                    return result
            self.fail("The desktop did not present the expected screenshot pixels.")

        def sample(frame, point):
            return frame.pixelColor(point * (frame.width() / editor.width()))

        try:
            editor.show()
            self.assertTrue(QTest.qWaitForWindowExposed(editor, 2000))
            def unchanged(frame):
                return sample(frame, QPoint(40, 140)) == dark and sample(frame, QPoint(445, 145)) == light

            screen_pixels(unchanged)
            mouse(QEvent.Type.MouseButtonPress, QPoint(90, 90), Qt.MouseButton.LeftButton)
            mouse(QEvent.Type.MouseMove, QPoint(330, 225))
            previous_edge = [QPoint(x, 150) for x in range(326, 335)]
            screen_pixels(lambda frame: unchanged(frame) and any(sample(frame, point) != light for point in previous_edge))
            mouse(QEvent.Type.MouseMove, QPoint(180, 225))
            screen_pixels(lambda frame: unchanged(frame) and all(sample(frame, point) == light for point in previous_edge))
        finally:
            editor.close()
