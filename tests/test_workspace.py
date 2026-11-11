import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from snaploom.app import Application
from snaploom.hotkeys import parse
from snaploom.imaging import pixels, stitch_images
from snaploom.storage import Workspace, save_image


class WorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setQuitOnLastWindowClosed(False)

    def test_clear_and_disable_history(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(Path(directory))
            image = QImage(31, 20, QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.green)
            region = QRect(-30, 20, 31, 20)
            try:
                workspace.set("history_limit", 2)
                for _ in range(3):
                    workspace.remember(image, region, "copy")
                self.assertEqual(workspace.regions(), [region, region])
                item_id = workspace.history()[0][0]
                self.assertEqual(workspace.history_image(item_id).pixelColor(0, 0).name(), "#00ff00")
                workspace.set("history_limit", 0)
                workspace.remember(image, region, "copy")
                self.assertEqual(workspace.history(), [])
            finally:
                workspace.close()

    def test_pin_restores_across_database_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = Application(self.qt, root, testing=True)
            image = QImage(44, 55, QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.red)
            try:
                pin_id = app.open_pin(image, QPoint(24, 26))
                window = app.pin_windows[pin_id]
                window.set_state("opacity", 54)
                window.set_state("horizontal", True)
                app.pin_action("hide", pin_id)
            finally:
                app.shutdown()
            restored = Application(self.qt, root, testing=True)
            try:
                window = restored.pin_windows[pin_id]
                self.assertFalse(window.isVisible())
                self.assertEqual(window.state["opacity"], 54)
                self.assertTrue(window.state["horizontal"])
                self.assertEqual(window.image.size(), image.size())
                restored.pin_action("show", pin_id)
                self.assertTrue(window.isVisible())
                restored.pin_action("delete", pin_id)
                self.assertEqual(list(restored.workspace.pins()), [])
            finally:
                restored.shutdown()

    def test_atomic_export(self):
        with tempfile.TemporaryDirectory() as directory:
            image = QImage(32, 22, QImage.Format.Format_ARGB32)
            image.fill(0xAA104080)
            path = save_image(image, Path(directory) / "nested" / "shot.png")
            result = QImage(str(path))
            self.assertEqual(result.pixel(0, 0), image.pixel(0, 0))

    def test_scroll_capture_recovers_overlap(self):
        import numpy as np

        rng = np.random.default_rng(23)
        rgb = rng.integers(0, 255, (260, 160, 3), dtype=np.uint8)
        document = QImage(rgb, 160, 260, 480, QImage.Format.Format_RGB888)
        first = document.copy(0, 0, 160, 160)
        second = document.copy(0, 55, 160, 160)
        result = stitch_images(first, second)
        self.assertEqual(result.height(), 215)
        self.assertTrue(np.array_equal(pixels(result), rgb[:215]))
        self.assertIsNone(stitch_images(first, first))

    def test_hotkey_parse(self):
        self.assertEqual(parse("Ctrl+Alt+S"), ({"ctrl", "alt"}, "s", 0x53))
        self.assertEqual(parse("Shift+F3"), ({"shift"}, "f3", 0x72))
        with self.assertRaises(ValueError):
            parse("Ctrl+Ctrl+F1")
        with self.assertRaises(ValueError):
            parse("a")


if __name__ == "__main__":
    unittest.main()
