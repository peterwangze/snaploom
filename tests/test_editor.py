import unittest

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from snaploom.capture import ScreenFrame, Snapshot
from snaploom.editor import Annotation, CaptureEditor, ImageEditor, render_annotations


class ObservedEditor(CaptureEditor):
    def paintEvent(self, event):
        self.painted_region = event.region()
        super().paintEvent(event)


class EditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setQuitOnLastWindowClosed(False)

    def make_image(self, width, height, color="#ffffff"):
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        image.fill(QColor(color))
        return image

    def test_overlay_keeps_native_screen_pixels_visible_before_and_during_drag(self):
        geometry = QRect(0, 0, 600, 440)
        ratio = self.qt.primaryScreen().devicePixelRatio()
        native = self.make_image(round(geometry.width() * ratio), round(geometry.height() * ratio), "#f2e5ca")
        dark = QColor("#101018")
        painter = QPainter(native)
        painter.fillRect(QRect(native.width() // 2, 0, native.width() // 2, native.height()), dark)
        painter.end()
        marker = QPoint(250, 35) * ratio
        native.setPixelColor(marker, QColor("#c74240"))
        snapshot = Snapshot(self.make_image(600, 440, "#556070"), geometry, (ScreenFrame(geometry, native),))
        editor = CaptureEditor(snapshot, windows=((QRect(60, 60, 450, 220), "Reference"),))
        light_point, dark_point = QPoint(140, 30), QPoint(470, 115)
        try:
            editor.show()
            QTest.mouseMove(editor, QPoint(120, 110))
            for phase in ("hover", "drag", "selection"):
                with self.subTest(phase=phase):
                    frame = editor.grab()
                    pixels = frame.toImage()
                    ratio = frame.devicePixelRatio()
                    self.assertEqual(pixels.pixelColor(light_point * ratio), QColor("#f2e5ca"))
                    self.assertEqual(pixels.pixelColor(dark_point * ratio), dark)
                    self.assertEqual(pixels.pixelColor(marker), QColor("#c74240"))
                    if phase == "hover":
                        rows = range(round(60 * ratio), round(64 * ratio))
                        light_edge = [pixels.pixelColor(round(150 * ratio), y) for y in rows]
                        dark_edge = [pixels.pixelColor(round(470 * ratio), y) for y in rows]
                        self.assertLess(min(color.lightness() for color in light_edge), 150)
                        self.assertGreater(max(color.lightness() for color in dark_edge), 130)
                if phase == "hover":
                    QTest.mousePress(editor, Qt.MouseButton.LeftButton, pos=QPoint(120, 110))
                    QTest.mouseMove(editor, QPoint(350, 230))
                elif phase == "drag":
                    QTest.mouseRelease(editor, Qt.MouseButton.LeftButton, pos=QPoint(350, 230))
            self.assertEqual(editor.selected_geometry(), QRect(QPoint(120, 110), QPoint(350, 230)))
        finally:
            editor.close()

    def test_drag_redraw_uses_only_changed_areas_and_matches_full_frame(self):
        if self.qt.platformName() != "offscreen":
            self.skipTest("reads the Qt offscreen backing store")
        geometry = QRect(0, 0, 620, 460)
        editor = ObservedEditor(Snapshot(self.make_image(620, 460), geometry))
        try:
            editor.show()
            self.qt.processEvents()
            start = QPoint(240, 200)
            QTest.mousePress(editor, Qt.MouseButton.LeftButton, pos=start)
            self.qt.processEvents()
            for cursor in (QPoint(90, 75), QPoint(178, 122), QPoint(380, 292), QPoint(279, 253)):
                with self.subTest(cursor=cursor):
                    QTest.mouseMove(editor, cursor)
                    self.qt.processEvents()
                    self.assertFalse(editor.painted_region.contains(QPoint(600, 400)))
                    self.assertTrue(editor.selected_geometry().contains(start))
                    self.assertTrue(editor.selected_geometry().contains(cursor))
                    incremental = editor.screen().grabWindow(editor.winId()).toImage()
                    self.assertFalse(incremental.isNull())
                    editor.repaint()
                    full_frame = editor.screen().grabWindow(editor.winId()).toImage()
                    self.assertEqual(incremental, full_frame)
            QTest.mouseRelease(editor, Qt.MouseButton.LeftButton, pos=QPoint(279, 253))
        finally:
            editor.close()

    def test_capture_magnifier_appears_on_alt_and_erases_when_released(self):
        geometry = QRect(0, 0, 620, 460)
        editor = CaptureEditor(Snapshot(self.make_image(620, 460), geometry))
        try:
            editor.show()
            QTest.mouseMove(editor, QPoint(190, 135))
            before = editor.grab().toImage()
            QTest.keyPress(editor, Qt.Key.Key_Alt)
            magnified = editor.grab().toImage()
            self.assertNotEqual(before, magnified)
            QTest.keyRelease(editor, Qt.Key.Key_Alt)
            self.assertEqual(editor.grab().toImage(), before)
        finally:
            editor.close()

    def test_region_move_and_resize_include_final_mouse_release_position(self):
        geometry = QRect(0, 0, 620, 460)
        initial = QRect(90, 60, 210, 140)
        editor = CaptureEditor(Snapshot(self.make_image(620, 460), geometry), selection=initial)
        try:
            editor.show()
            start = initial.center()
            delta = QPoint(37, 27)
            QTest.mousePress(editor, Qt.MouseButton.LeftButton, pos=start)
            self.assertFalse(editor.toolbar.isVisible())
            QTest.mouseRelease(editor, Qt.MouseButton.LeftButton, pos=start + delta)
            moved = initial.translated(delta)
            self.assertEqual(editor.selected_geometry(), moved)
            self.assertTrue(editor.toolbar.isVisible())
            QTest.mousePress(editor, Qt.MouseButton.LeftButton, pos=moved.bottomRight())
            self.assertFalse(editor.toolbar.isVisible())
            QTest.mouseRelease(editor, Qt.MouseButton.LeftButton, pos=moved.bottomRight() + delta)
            self.assertEqual(editor.selected_geometry(), moved.adjusted(0, 0, delta.x(), delta.y()))
            self.assertTrue(editor.toolbar.isVisible())
        finally:
            editor.close()

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
