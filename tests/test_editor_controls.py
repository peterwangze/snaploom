import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QColorDialog, QMenu, QStyle, QStyleOptionSpinBox

from snaploom.capture import Snapshot
from snaploom.editor import CaptureEditor, ImageEditor


class EditorControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        if cls.qt.platformName() != "offscreen":
            raise unittest.SkipTest("Use QT_QPA_PLATFORM=offscreen to isolate capture editor interactions.")
        cls.qt.setQuitOnLastWindowClosed(False)

    def open_editor(self, geometry=None, **options):
        bounds = geometry or self.qt.primaryScreen().availableGeometry()
        image = QImage(bounds.size(), QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
        selection = QRect(bounds.topLeft() + QPoint(22, 30), bounds.size() / 2)
        editor = CaptureEditor(Snapshot(image, bounds), selection=selection, options={"locale": "zh-CN", **options})
        self.addCleanup(self.close_widget, editor)
        editor.show()
        self.qt.processEvents()
        self.assertTrue(editor.toolbar.isVisible())
        return editor

    def close_widget(self, widget):
        widget.close()
        widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.qt.processEvents()

    def click(self, widget, position=None):
        point = widget.rect().center() if position is None else position
        desktop_point = widget.mapToGlobal(point)
        hit = self.qt.widgetAt(desktop_point)
        self.assertTrue(hit is widget or (hit is not None and widget.isAncestorOf(hit)),
                        f"The control {widget.accessibleName() or type(widget).__name__} is covered or clipped.")
        window = widget.window()
        QTest.mouseClick(window.windowHandle(), Qt.MouseButton.LeftButton, pos=window.mapFromGlobal(desktop_point))
        self.qt.processEvents()

    def assert_cursor_at(self, widget, cursor, position=None):
        point = widget.rect().center() if position is None else position
        desktop_point = widget.mapToGlobal(point)
        window = widget.window()
        QTest.mouseMove(window.windowHandle(), window.mapFromGlobal(desktop_point))
        self.qt.processEvents()
        hit = self.qt.widgetAt(desktop_point)
        self.assertIs(hit, widget)
        self.assertEqual(hit.cursor().shape(), cursor)

    def drag(self, editor, start, end):
        window = editor.window()
        handle = window.windowHandle()
        start = window.mapFromGlobal(editor.mapToGlobal(start))
        end = window.mapFromGlobal(editor.mapToGlobal(end))
        QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(handle, end)
        QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=end)
        self.qt.processEvents()

    def type_number(self, spin, digits, key=Qt.Key.Key_Return):
        field = spin.lineEdit()
        self.click(field)
        focus = self.qt.focusWidget()
        self.assertTrue(focus is spin or (focus is not None and spin.isAncestorOf(focus)))
        window = field.window().windowHandle()
        QTest.keyClick(window, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        for digit in str(digits):
            QTest.keyClick(window, ord(digit))
        QTest.keyClick(window, key)
        self.qt.processEvents()

    def choose_style(self, editor, style):
        combo = editor.style
        row = combo.findData(style)
        self.assertGreaterEqual(row, 0)
        self.click(combo)
        view = combo.view()
        self.assertTrue(view.isVisible())
        index = view.model().index(row, 0)
        point = view.visualRect(index).intersected(view.viewport().rect()).center()
        self.click(view.viewport(), point)
        self.assertEqual(combo.currentData(), style)

    def test_cursor_switches_between_canvas_toolbar_and_annotation_controls(self):
        cross = Qt.CursorShape.CrossCursor
        arrow = Qt.CursorShape.ArrowCursor
        text = Qt.CursorShape.IBeamCursor
        for bounds in (None, QRect(0, 0, 220, 145)):
            with self.subTest(floating=bounds is not None):
                editor = self.open_editor(bounds)
                self.assertEqual(editor.toolbar.isWindow(), bounds is not None)
                canvas_point = QPoint(100, 80)
                self.assert_cursor_at(editor, cross, canvas_point)
                self.assert_cursor_at(editor.toolbar, arrow, QPoint(4, 4))
                self.assert_cursor_at(editor.tool_buttons["arrow"], arrow)
                self.click(editor.tool_buttons["arrow"])
                self.assert_cursor_at(editor.stroke_width.lineEdit(), text)
                self.assert_cursor_at(editor.style, arrow)
                self.click(editor.style)
                view = editor.style.view()
                self.assertTrue(view.isVisible())
                self.assert_cursor_at(view.viewport(), arrow)
                editor.style.hidePopup()
                self.assertFalse(view.isVisible())
                self.assert_cursor_at(editor, cross, canvas_point)
                editor.close()

    def test_line_width_buttons_and_typing_keep_capture_open_and_affect_pixels(self):
        editor = self.open_editor()
        completed = []
        editor.completed.connect(lambda *args: completed.append(args))
        self.click(editor.tool_buttons["pen"])
        width = editor.stroke_width
        initial = width.value()
        style_option = QStyleOptionSpinBox()
        width.initStyleOption(style_option)
        up = width.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, style_option,
                                          QStyle.SubControl.SC_SpinBoxUp, width)
        self.click(width, up.center())
        self.assertEqual(width.value(), initial + 1)
        self.type_number(width, 24)
        self.assertEqual(width.value(), 24)
        self.assertTrue(editor.isVisible())
        self.assertEqual(completed, [])
        self.drag(editor, QPoint(44, 79), QPoint(125, 79))
        self.assertEqual(editor.document.annotations[0].width, 24)
        image = editor.export_image()
        self.assertNotEqual(image.pixelColor(50, 57), QColor(Qt.GlobalColor.white))

    def test_editing_existing_width_is_one_undo_step(self):
        editor = self.open_editor()
        self.click(editor.tool_buttons["rectangle"])
        self.drag(editor, QPoint(48, 55), QPoint(135, 112))
        self.click(editor.tool_buttons["ellipse"])
        self.drag(editor, QPoint(155, 60), QPoint(198, 107))
        original, other = editor.document.annotations
        self.click(editor.tool_buttons["select"])
        self.click(editor, QPoint(75, 80))
        self.assertEqual(editor.stroke_width.value(), original.width)
        changed_tool_styles = []
        editor.annotation_style_changed.connect(lambda *args: changed_tool_styles.append(args))
        self.type_number(editor.stroke_width, 24)
        self.assertTrue(editor.isVisible())
        updated, unchanged = editor.document.annotations
        self.assertEqual(updated.width, 24)
        self.assertEqual(unchanged, other)
        self.assertEqual(changed_tool_styles, [])
        self.click(editor.undo_button)
        self.assertEqual(editor.document.annotations, (original, other))
        self.click(editor.redo_button)
        self.assertEqual(editor.document.annotations, (updated, other))

    def test_effect_strength_changes_pixels_and_eraser_restores_the_source(self):
        editor = self.open_editor()
        geometry = editor.snapshot.geometry
        image = QImage(geometry.size(), QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
        painter = QPainter(image)
        for x in range(0, image.width(), 8):
            painter.fillRect(x, 0, 4, image.height(), QColor("#263a60"))
        painter.end()
        editor.set_snapshot(Snapshot(image, geometry))
        source = editor.export_image()
        mosaic_region = QRect(30, 38, 50, 25)
        blur_region = QRect(134, 42, 50, 25)

        self.click(editor.tool_buttons["mosaic"])
        self.assertEqual(editor.stroke_width.accessibleName(), "马赛克强度")
        self.type_number(editor.stroke_width, 7)
        self.drag(editor, QPoint(48, 65), QPoint(130, 105))
        self.assertEqual(editor.document.annotations[-1].width, 7)
        self.assertNotEqual(editor.export_image().copy(mosaic_region), source.copy(mosaic_region))

        self.click(editor.tool_buttons["blur"])
        self.assertEqual(editor.stroke_width.accessibleName(), "模糊强度")
        self.type_number(editor.stroke_width, 11)
        self.drag(editor, QPoint(150, 70), QPoint(210, 113))
        self.assertEqual(editor.document.annotations[-1].width, 11)
        self.assertNotEqual(editor.export_image().copy(blur_region), source.copy(blur_region))

        self.click(editor.tool_buttons["erase"])
        self.assertFalse(editor.stroke_width.isVisible())
        self.drag(editor, QPoint(45, 63), QPoint(133, 107))
        self.assertEqual(editor.export_image().copy(mosaic_region), source.copy(mosaic_region))

    def test_escape_cancels_width_input_before_cancelling_capture(self):
        editor = self.open_editor()
        self.click(editor.tool_buttons["pen"])
        width = editor.stroke_width.value()
        self.type_number(editor.stroke_width, 24, Qt.Key.Key_Escape)
        self.assertEqual(editor.stroke_width.value(), width)
        self.assertTrue(editor.isVisible())
        QTest.keyClick(editor.windowHandle(), Qt.Key.Key_Escape)
        self.assertFalse(editor.isVisible())

    def test_tab_and_arrow_keys_adjust_width_without_moving_selection(self):
        editor = self.open_editor()
        bounds = editor.selected_geometry()
        width = editor.stroke_width.value()
        for _ in range(len(editor.tool_buttons) + 5):
            QTest.keyClick(editor.windowHandle(), Qt.Key.Key_Tab)
            if editor.stroke_width.hasFocus():
                break
        self.assertTrue(editor.stroke_width.hasFocus())
        QTest.keyClick(editor.windowHandle(), Qt.Key.Key_Up)
        self.assertEqual(editor.stroke_width.value(), width + 1)
        self.assertEqual(editor.selected_geometry(), bounds)
        QTest.keyClick(editor.windowHandle(), Qt.Key.Key_Return)
        self.assertTrue(editor.isVisible())
        QTest.keyClick(editor.windowHandle(), Qt.Key.Key_Right)
        self.assertEqual(editor.selected_geometry(), bounds.translated(1, 0))

    def test_tool_colors_widths_and_styles_are_remembered_when_switching_tools(self):
        editor = self.open_editor()
        self.click(editor.tool_buttons["arrow"])
        self.type_number(editor.stroke_width, 12)
        self.choose_style(editor, "double")
        green = QColor("#228b55")
        with patch.object(QColorDialog, "getColor", return_value=green) as choose:
            self.click(editor.color_button)
        choose.assert_called_once()
        self.click(editor.tool_buttons["rectangle"])
        self.type_number(editor.stroke_width, 5)
        self.choose_style(editor, "dashed")
        self.click(editor.fill)
        blue = QColor("#3464c8")
        with patch.object(QColorDialog, "getColor", return_value=blue):
            self.click(editor.color_button)
        self.drag(editor, QPoint(45, 70), QPoint(97, 106))
        self.click(editor.tool_buttons["arrow"])
        self.assertEqual(editor.stroke_width.value(), 12)
        self.assertEqual(editor.style.currentData(), "double")
        self.drag(editor, QPoint(120, 70), QPoint(180, 106))
        rectangle, arrow = editor.document.annotations
        self.assertEqual((rectangle.width, rectangle.style, rectangle.fill), (5, "dashed", True))
        self.assertEqual(QColor(rectangle.color), blue)
        self.assertEqual((arrow.width, arrow.style), (12, "double"))
        self.assertEqual(QColor(arrow.color), green)

    def test_undo_and_redo_buttons_change_one_mark_and_new_mark_branches_history(self):
        editor = self.open_editor()
        self.assertEqual(editor.undo_button.text(), "撤销一步")
        self.assertEqual(editor.redo_button.text(), "重做一步")
        self.assertFalse(editor.undo_button.isEnabled())
        self.assertFalse(editor.redo_button.isEnabled())
        self.click(editor.tool_buttons["rectangle"])
        self.drag(editor, QPoint(45, 55), QPoint(95, 100))
        first = editor.document.annotations
        self.click(editor.tool_buttons["ellipse"])
        self.drag(editor, QPoint(112, 60), QPoint(159, 105))
        both = editor.document.annotations
        self.assertEqual(len(both), 2)
        self.click(editor.undo_button)
        self.assertEqual(editor.document.annotations, first)
        self.assertTrue(editor.redo_button.isEnabled())
        self.click(editor.redo_button)
        self.assertEqual(editor.document.annotations, both)
        QTest.keyClick(editor.windowHandle(), Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(editor.document.annotations, first)
        QTest.keyClick(editor.windowHandle(), Qt.Key.Key_Z,
                       Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(editor.document.annotations, both)
        self.click(editor.undo_button)
        self.click(editor.tool_buttons["pen"])
        self.drag(editor, QPoint(119, 69), QPoint(165, 111))
        self.assertEqual([item.tool for item in editor.document.annotations], ["rectangle", "pen"])
        self.assertFalse(editor.redo_button.isEnabled())
        self.click(editor.undo_button)
        self.assertEqual(editor.document.annotations, first)
        self.click(editor.undo_button)
        self.assertEqual(editor.document.annotations, ())
        self.assertFalse(editor.undo_button.isEnabled())

    def test_default_width_applies_to_unused_tools_and_saved_tool_width_wins(self):
        editor = self.open_editor(annotation_width=7, annotation_styles={
            "arrow": {"width": 11, "color": "#228b55", "style": "double", "fill": False},
        })
        defaults = []
        editor.annotation_width_changed.connect(defaults.append)
        self.assertEqual(editor.stroke_width.value(), 7)
        self.assertTrue(editor.stroke_width.isEnabled())
        self.type_number(editor.stroke_width, 9)
        self.assertEqual(defaults, [9])
        self.click(editor.tool_buttons["rectangle"])
        self.assertEqual(editor.stroke_width.value(), 9)
        self.click(editor.tool_buttons["arrow"])
        self.assertEqual(editor.stroke_width.value(), 11)
        self.assertEqual(editor.style.currentData(), "double")
        self.drag(editor, QPoint(45, 70), QPoint(116, 103))
        self.assertEqual(QColor(editor.document.annotations[0].color), QColor("#228b55"))

    def test_enter_in_next_number_field_only_applies_the_number(self):
        editor = self.open_editor()
        self.click(editor.tool_buttons["number"])
        self.type_number(editor.number_spin, 12)
        self.assertTrue(editor.isVisible())
        self.click(editor, QPoint(100, 90))
        self.assertEqual(editor.document.annotations[0].text, "12")
        self.assertEqual(editor.number_spin.value(), 13)

    def test_enter_in_embedded_editor_applies_width_without_applying_image(self):
        image = QImage(220, 150, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
        dialog = ImageEditor(image, locale="zh-CN")
        self.addCleanup(self.close_widget, dialog)
        available = self.qt.primaryScreen().availableGeometry()
        dialog.resize(min(available.width(), 650), min(available.height(), 480))
        applied = []
        dialog.applied.connect(applied.append)
        dialog.show()
        self.qt.processEvents()
        self.assert_cursor_at(dialog.canvas, Qt.CursorShape.CrossCursor)
        self.assert_cursor_at(dialog.canvas.tool_buttons["pen"], Qt.CursorShape.ArrowCursor)
        self.click(dialog.canvas.tool_buttons["pen"])
        self.assert_cursor_at(dialog.canvas.stroke_width.lineEdit(), Qt.CursorShape.IBeamCursor)
        self.type_number(dialog.canvas.stroke_width, 12)
        self.assertEqual(dialog.canvas.stroke_width.value(), 12)
        self.assertTrue(dialog.isVisible())
        self.assertEqual(applied, [])
        self.drag(dialog.canvas, QPoint(28, 40), QPoint(94, 67))
        self.click(dialog.apply_button)
        self.assertEqual(len(applied), 1)
        self.assertEqual(dialog.annotations[0].width, 12)

    def test_undo_removes_the_latest_polyline_point_before_committed_marks(self):
        editor = self.open_editor()
        self.click(editor.tool_buttons["rectangle"])
        self.drag(editor, QPoint(43, 52), QPoint(85, 93))
        first, = editor.document.annotations
        self.click(editor.tool_buttons["line"])
        for point in (QPoint(101, 68), QPoint(127, 98), QPoint(168, 76)):
            self.click(editor, point)
        QTest.keyClick(editor.windowHandle(), Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(editor.document.annotations, (first,))
        QTest.keyClick(editor.windowHandle(), Qt.Key.Key_Return)
        self.assertEqual(len(editor.document.annotations), 2)
        _, line = editor.document.annotations
        offset = editor.selected_geometry().topLeft()
        self.assertEqual(line.points, tuple((p.x() - offset.x(), p.y() - offset.y()) for p in (QPoint(101, 68), QPoint(127, 98))))

    def test_clear_all_is_a_separate_action_that_can_be_undone_once(self):
        editor = self.open_editor()
        self.click(editor.tool_buttons["rectangle"])
        self.drag(editor, QPoint(44, 52), QPoint(87, 92))
        self.click(editor.tool_buttons["ellipse"])
        self.drag(editor, QPoint(103, 66), QPoint(158, 111))
        before = editor.document.annotations
        self.assertEqual(len(before), 2)
        clear_labels = []

        def choose_clear():
            menu = self.qt.activePopupWidget()
            if isinstance(menu, QMenu):
                try:
                    clear = next(action for action in menu.actions() if "清空" in action.text())
                    clear_labels.append(clear.text())
                    clear.trigger()
                finally:
                    menu.hide()

        QTimer.singleShot(0, choose_clear)
        QTest.mouseClick(editor.windowHandle(), Qt.MouseButton.RightButton, pos=QPoint(220, 122))
        self.assertEqual(len(clear_labels), 1)
        self.assertEqual(editor.document.annotations, ())
        self.click(editor.undo_button)
        self.assertEqual(editor.document.annotations, before)

    def test_toolbar_controls_are_reachable_on_a_small_screen(self):
        screen_bounds = self.qt.primaryScreen().availableGeometry()
        bounds = QRect(screen_bounds.x(), screen_bounds.y(), min(480, screen_bounds.width()), min(360, screen_bounds.height()))

        class SmallScreen:
            def availableGeometry(self):
                return bounds

            def geometry(self):
                return bounds

            def devicePixelRatio(self):
                return 1.0

        with patch.object(QApplication, "screenAt", return_value=SmallScreen()):
            editor = self.open_editor(bounds)
            self.click(editor.tool_buttons["arrow"])
            self.type_number(editor.stroke_width, 8)
            for control in (editor.stroke_width, editor.color_button, editor.style, editor.undo_button, editor.redo_button):
                self.assertTrue(control.isVisible())
                self.assertTrue(bounds.contains(QRect(control.mapToGlobal(QPoint()), control.size())), control.accessibleName())
            self.assertEqual(editor.stroke_width.value(), 8)


if __name__ == "__main__":
    unittest.main()
