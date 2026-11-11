import unittest

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from snaploom.capture import ScreenFrame, Snapshot
from snaploom.editor import Annotation, CaptureEditor, ImageEditor, render_annotations


class EditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setQuitOnLastWindowClosed(False)

    def make_image(self, width, height, color="#ffffff"):
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        image.fill(QColor(color))
        return image

    def test_annotation_export_matches_native_resolution_with_negative_screen_origin(self):
        original = self.make_image(600, 500)
        frame = ScreenFrame(QRect(-300, -250, 300, 250), original)
        snapshot = Snapshot(original.scaled(300, 250), frame.geometry, (frame,))
        selected = QRect(-280, -230, 120, 100)
        editor = CaptureEditor(snapshot, selection=selected)
        try:
            editor._set_tool("rectangle")
            QTest.mousePress(editor, Qt.MouseButton.LeftButton, pos=QPoint(30, 30))
            QTest.mouseMove(editor, QPoint(95, 85))
            QTest.mouseRelease(editor, Qt.MouseButton.LeftButton, pos=QPoint(95, 85))
            image = editor.export_image()
            self.assertEqual(editor.selected_geometry(), selected)
            self.assertEqual((image.width(), image.height()), (240, 200))
            self.assertEqual(image.pixelColor(20, 20).name(), "#ff5b55")
            editor._undo()
            self.assertEqual(editor.export_image().pixelColor(20, 20).name(), "#ffffff")
            editor._redo()
            self.assertEqual(editor.export_image().pixelColor(20, 20).name(), "#ff5b55")
        finally:
            editor.close()

    def test_save_output_keeps_editor_available_until_dialog_succeeds(self):
        snapshot = Snapshot(self.make_image(350, 230), QRect(0, 0, 350, 230))
        selection = QRect(12, 16, 33, 44)
        editor = CaptureEditor(snapshot, selection=selection)
        calls = []
        editor.completed.connect(lambda *values: calls.append(values))
        try:
            editor._complete("save")
            self.assertEqual(calls[0][0], "save")
            self.assertEqual(calls[0][2], selection)
            self.assertFalse(editor.finished)
            editor._complete("copy")
            self.assertEqual(calls[1][0], "copy")
            self.assertTrue(editor.finished)
        finally:
            editor.close()

    def test_color_pick_uses_native_pixel_and_leaves_selection_empty(self):
        pixels = self.make_image(400, 300, "#e2f4ff")
        snapshot = Snapshot(
            self.make_image(200, 150),
            QRect(0, 0, 200, 150),
            (ScreenFrame(QRect(0, 0, 200, 150), pixels),),
        )
        editor = CaptureEditor(snapshot, options={"color_only": True})
        picked = []
        editor.color_copied.connect(picked.append)
        try:
            QTest.mouseClick(editor, Qt.MouseButton.LeftButton, pos=QPoint(30, 30))
            self.assertEqual(picked, ["#E2F4FF"])
            self.assertTrue(editor.selected_geometry().isEmpty())
        finally:
            editor.close()

    def test_crop_flattens_edit_scene_and_covers_original_pixels(self):
        source = self.make_image(300, 250)
        annotations = (Annotation("redact", ((30, 40), (160, 130)), color="#172334"),)
        redacted = render_annotations(source, annotations)
        self.assertEqual(redacted.pixelColor(40, 50), QColor("#172334"))
        dialog = ImageEditor(source, annotations=annotations)
        dialog.activate_crop()
        dialog.canvas._set_selection(QRect(25, 30, 150, 100))
        dialog.accept()
        self.assertEqual(dialog.result_base.size(), dialog.result_image.size())
        self.assertEqual(dialog.annotations, ())
        self.assertEqual(dialog.result_image.pixelColor(15, 20), QColor("#172334"))
        self.assertEqual(dialog.result_image.pixelColor(0, 0), QColor("#ffffff"))

    def test_restored_numbering_series_continue_independently(self):
        annotations = (
            Annotation("number", ((40, 50),), text="5", series="Review"),
            Annotation("number", ((100, 40),), text="2"),
        )
        dialog = ImageEditor(self.make_image(260, 150), annotations=annotations)
        editor = dialog.canvas
        editor._set_tool("number")
        editor.number_group.setCurrentIndex(editor.number_group.findData("Review"))
        QTest.mouseClick(editor, Qt.MouseButton.LeftButton, pos=QPoint(150, 75))
        editor.number_group.setCurrentIndex(editor.number_group.findData(""))
        QTest.mouseClick(editor, Qt.MouseButton.LeftButton, pos=QPoint(180, 75))
        dialog.accept()
        self.assertEqual([(item.series, item.text) for item in dialog.annotations[-2:]], [("Review", "6"), ("", "3")])


if __name__ == "__main__":
    unittest.main()
