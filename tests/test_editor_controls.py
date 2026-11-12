import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QColorDialog, QFontDialog, QInputDialog, QMenu, QStyle, QStyleOptionSpinBox, QToolButton

from snaploom.capture import Snapshot
from snaploom.editor import CaptureEditor, ImageEditor


class EditorControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        if cls.qt.platformName() != "offscreen":
            raise unittest.SkipTest("Use QT_QPA_PLATFORM=offscreen to isolate capture editor interactions.")
        cls.qt.setQuitOnLastWindowClosed(False)

    def open_editor(self, geometry=None, *, selection=None, history=(), windows=(), **options):
        bounds = geometry or self.qt.primaryScreen().availableGeometry()
        image = QImage(bounds.size(), QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
        if selection is None:
            selection = QRect(bounds.topLeft() + QPoint(22, 30), bounds.size() / 2)
        editor = CaptureEditor(Snapshot(image, bounds), selection=selection, history=history, windows=windows,
                               options={"locale": "zh-CN", **options})
        self.addCleanup(self.close_widget, editor)
        editor.show()
        self.qt.processEvents()
        self.assertEqual(editor.toolbar.isVisible(), not selection.isEmpty() and not options.get("color_only", False))
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

    def choose_menu_item(self, editor, label, *, context_point=None):
        errors = []
        clicked = []

        def choose(menu):
            try:
                action = next((item for item in menu.actions() if item.text() == label), None)
                self.assertIsNotNone(action, label)
                self.assertTrue(action.isEnabled())
                QTest.mouseClick(menu.windowHandle(), Qt.MouseButton.LeftButton, pos=menu.actionGeometry(action).center())
                clicked.append(label)
            except Exception as error:
                errors.append(error)
            finally:
                if menu.isVisible():
                    menu.hide()

        if context_point is None:
            more = next(button for button in editor.toolbar.findChildren(QToolButton) if button.menu() is not None)
            menu = more.menu()
            schedule = lambda: QTimer.singleShot(0, lambda: choose(menu))
            menu.aboutToShow.connect(schedule)
            try:
                self.click(more)
            finally:
                menu.aboutToShow.disconnect(schedule)
        else:
            build_menu = editor._edit_menu

            def choose_when_shown(item):
                menu = build_menu(item)
                menu.aboutToShow.connect(lambda: QTimer.singleShot(0, lambda: choose(menu)))
                return menu

            with patch.object(editor, "_edit_menu", side_effect=choose_when_shown):
                QTest.mouseClick(editor.windowHandle(), Qt.MouseButton.RightButton, pos=context_point)
            self.qt.processEvents()
        if errors:
            raise errors[0]
        self.assertEqual(clicked, [label])

    def test_every_tool_button_and_default_shortcut_select_the_same_tool(self):
        editor = self.open_editor()
        completed, cancelled = [], []
        editor.completed.connect(lambda *args: completed.append(args))
        editor.cancelled.connect(lambda: cancelled.append(True))
        selection = editor.selected_geometry()
        tools = {
            "select": "V", "rectangle": "R", "ellipse": "E", "line": "L", "arrow": "A", "pen": "B",
            "marker": "H", "text": "T", "number": "N", "mosaic": "M", "blur": "G", "redact": "D",
            "erase": "X", "magnify": "Z",
        }
        self.assertEqual(set(editor.tool_buttons), set(tools))
        for tool, shortcut in tools.items():
            with self.subTest(tool=tool):
                other = "pen" if tool == "select" else "select"
                self.click(editor.tool_buttons[other])
                self.click(editor.tool_buttons[tool])
                self.assertEqual(editor.tool, tool)
                self.assertTrue(editor.tool_buttons[tool].isChecked())
                self.click(editor.tool_buttons[other])
                QTest.keyClick(editor.windowHandle(), ord(shortcut))
                self.assertEqual(editor.tool, tool)
                self.assertEqual([name for name, button in editor.tool_buttons.items() if button.isChecked()], [tool])
                self.assertEqual(editor.selected_geometry(), selection)
                self.assertTrue(editor.isVisible())
        self.assertEqual(editor.document.annotations, ())
        self.assertEqual(completed, [])
        self.assertEqual(cancelled, [])

    def test_drag_tools_render_their_effect_and_undo_restores_the_source(self):
        editor = self.open_editor()
        geometry = editor.snapshot.geometry
        image = QImage(geometry.size(), QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
        painter = QPainter(image)
        for x in range(0, image.width(), 8):
            painter.fillRect(x, 0, 4, image.height(), QColor("#263a60"))
        painter.end()
        editor.set_snapshot(Snapshot(image, geometry))
        baseline = editor.export_image()

        for tool in ("rectangle", "ellipse", "arrow", "pen", "marker", "mosaic", "blur", "redact", "magnify"):
            with self.subTest(tool=tool):
                self.click(editor.tool_buttons[tool])
                self.drag(editor, QPoint(52, 62), QPoint(142, 121))
                annotation, = editor.document.annotations
                self.assertEqual(annotation.tool, tool)
                self.assertNotEqual(editor.export_image(), baseline)
                self.click(editor.undo_button)
                self.assertEqual(editor.document.annotations, ())
                self.assertEqual(editor.export_image(), baseline)

    def test_numbering_series_creation_and_switching_preserve_independent_counts(self):
        editor = self.open_editor()
        self.click(editor.tool_buttons["number"])
        self.click(editor, QPoint(62, 62))
        groups = editor.number_group

        def choose_series(series):
            row = groups.count() - 1 if series is None else groups.findData(series)
            self.assertGreaterEqual(row, 0)
            self.click(groups)
            view = groups.view()
            self.assertTrue(view.isVisible())
            point = view.visualRect(view.model().index(row, 0)).intersected(view.viewport().rect()).center()
            self.click(view.viewport(), point)

        with patch.object(QInputDialog, "getText", side_effect=[("", False), ("Review", True)]):
            choose_series(None)
            self.assertEqual(groups.currentData(), "")
            self.assertEqual(editor.number_spin.value(), 2)
            choose_series(None)
        self.assertEqual(groups.currentData(), "Review")
        self.assertEqual(editor.number_spin.value(), 1)
        self.type_number(editor.number_spin, 12)
        self.click(editor, QPoint(105, 70))
        choose_series("")
        self.assertEqual(editor.number_spin.value(), 2)
        self.click(editor, QPoint(147, 81))
        self.assertEqual([(item.series, item.text) for item in editor.document.annotations],
                         [("", "1"), ("Review", "12"), ("", "2")])

    def test_text_and_context_edits_only_apply_to_the_selected_annotation(self):
        editor = self.open_editor()
        self.click(editor.tool_buttons["text"])
        with patch.object(QInputDialog, "getMultiLineText", side_effect=[("discard", False), ("Review notes", True)]):
            self.click(editor, QPoint(64, 75))
            self.assertEqual(editor.document.annotations, ())
            self.click(editor, QPoint(64, 75))
        text, = editor.document.annotations
        self.assertEqual(text.text, "Review notes")
        self.click(editor.tool_buttons["rectangle"])
        self.click(editor.fill)
        self.drag(editor, QPoint(140, 96), QPoint(205, 135))
        self.assertTrue(editor.document.annotations[-1].fill)
        self.click(editor.tool_buttons["select"])
        self.choose_menu_item(editor, "向右旋转所选", context_point=QPoint(175, 111))
        rotated = editor.document.annotations[-1]
        self.assertEqual(rotated.angle, 15)
        self.assertEqual(editor.document.annotations[0], text)
        self.choose_menu_item(editor, "删除所选标注", context_point=QPoint(175, 111))
        self.assertEqual(editor.document.annotations, (text,))
        QTest.keyClick(editor.windowHandle(), Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(editor.document.annotations, (text, rotated))

    def test_text_appearance_and_number_context_commands_apply_to_the_selected_mark(self):
        editor = self.open_editor()
        text_point, number_point = QPoint(49, 56), QPoint(172, 106)
        self.click(editor.tool_buttons["text"])
        with patch.object(QInputDialog, "getMultiLineText", return_value=("Review", True)):
            self.click(editor, text_point)
        self.click(editor.tool_buttons["number"])
        self.click(editor, number_point)
        _, number = editor.document.annotations
        self.click(editor.tool_buttons["select"])

        def activate(*labels):
            menu = editor._edit_menu(editor._selected_annotation())
            try:
                submenu = menu
                for label in labels[:-1]:
                    parent_action = next(item for item in submenu.actions() if item.text() == label)
                    submenu = parent_action.menu()
                next(item for item in submenu.actions() if item.text() == labels[-1]).trigger()
            finally:
                menu.deleteLater()

        self.click(editor, text_point + QPoint(3, 3))
        with patch.object(QFontDialog, "getFont", return_value=(True, QFont("Arial"))):
            activate("文字外观", "字体")
        with (
            patch.object(QColorDialog, "getColor", side_effect=[QColor("#dcb854"), QColor("#1f3040")]),
            patch.object(QInputDialog, "getDouble", return_value=(3.5, True)),
        ):
            activate("文字外观", "背景颜色")
            activate("文字外观", "描边颜色与宽度")
        text, unchanged_number = editor.document.annotations
        self.assertEqual(unchanged_number, number)
        self.assertEqual(text.font_family, "Arial")
        self.assertEqual((QColor(text.background), QColor(text.outline), text.outline_width),
                         (QColor("#dcb854"), QColor("#1f3040"), 3.5))
        activate("文字外观", "清除背景和描边")
        text, _ = editor.document.annotations
        self.assertEqual((text.background, text.outline), ("", ""))

        self.click(editor, number_point)
        editor._next_numbers["Review"] = 1
        with patch.object(QInputDialog, "getInt", return_value=(7, True)):
            activate("编辑步骤序号")
        activate("移到编号分组", "Review")
        unchanged_text, number = editor.document.annotations
        self.assertEqual(unchanged_text, text)
        self.assertEqual((number.text, number.series, editor._next_numbers["Review"]), ("7", "Review", 8))

    def test_more_menu_geometry_commands_change_selection_and_export(self):
        editor = self.open_editor()
        original = editor.selected_geometry()
        with patch.object(QInputDialog, "getText", side_effect=[("3:2", False), ("3:2", True), ("", True)]):
            self.choose_menu_item(editor, "锁定选区比例")
            self.assertEqual(editor.selected_geometry(), original)
            self.choose_menu_item(editor, "锁定选区比例")
            self.assertAlmostEqual(editor.selection.width() / editor.selection.height(), 1.5, places=2)
            self.choose_menu_item(editor, "锁定选区比例")
            self.assertEqual(editor.aspect, 0)

        scale = editor.screen().devicePixelRatio()
        size = (120, 80)
        with patch.object(QInputDialog, "getInt", side_effect=[
            (round(size[0] * scale), True), (round(size[1] * scale), True), (14, True),
        ]):
            self.choose_menu_item(editor, "设置选区尺寸")
            self.assertEqual(editor.selection.size().toTuple(), size)
            self.assertEqual(editor.export_image().pixelColor(0, 0).alpha(), 255)
            self.choose_menu_item(editor, "导出圆角")
        image = editor.export_image()
        self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
        self.assertEqual(image.pixelColor(image.width() // 2, image.height() // 2).alpha(), 255)

    def test_tab_and_enter_activate_toolbar_controls_in_each_window_layout(self):
        image = QImage(400, 260, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
        dialog = ImageEditor(image)
        self.addCleanup(self.close_widget, dialog)

        for editor in (self.open_editor(), self.open_editor(QRect(0, 0, 200, 158)), dialog.canvas):
            with self.subTest(floating=editor.toolbar.isWindow(), embedded=editor is dialog.canvas):
                host = editor.window()
                host.show()
                host.activateWindow()
                editor.setFocus()
                self.qt.processEvents()
                window = host.windowHandle()
                completed = []
                if editor is dialog.canvas:
                    dialog.applied.connect(lambda *args: completed.append(args))
                else:
                    editor.completed.connect(lambda *args: completed.append(args))

                target = editor.tool_buttons["rectangle"]
                for _ in range(40):
                    active = self.qt.activeWindow()
                    QTest.keyClick(active.windowHandle(), Qt.Key.Key_Tab)
                    self.qt.processEvents()
                    if self.qt.focusWidget() is target:
                        break
                self.assertIs(self.qt.focusWidget(), target)
                QTest.keyClick(target.window().windowHandle(), Qt.Key.Key_Return)
                self.qt.processEvents()
                self.assertTrue(host.isVisible())
                self.assertEqual(editor.tool, "rectangle")
                self.assertEqual(completed, [])
                self.assertIs(self.qt.focusWidget(), editor)
                host.close()

    def test_enter_opens_focused_more_menu_and_escape_only_dismisses_it(self):
        editor = self.open_editor()
        more = next(button for button in editor.toolbar.findChildren(QToolButton) if button.menu() is not None)
        selection = editor.selected_geometry()
        for _ in range(40):
            QTest.keyClick(self.qt.activeWindow().windowHandle(), Qt.Key.Key_Tab)
            self.qt.processEvents()
            if self.qt.focusWidget() is more:
                break
        self.assertIs(self.qt.focusWidget(), more)
        menu = more.menu()
        QTest.keyClick(more.window().windowHandle(), Qt.Key.Key_Return)
        self.qt.processEvents()
        self.assertTrue(menu.isVisible())
        QTest.keyClick(menu.windowHandle(), Qt.Key.Key_Escape)
        self.qt.processEvents()
        self.assertFalse(menu.isVisible())
        self.assertTrue(editor.isVisible())
        self.assertEqual(editor.selected_geometry(), selection)

    def test_window_suggestions_cycle_with_tab_and_vertical_wheel(self):
        front = QRect(33, 34, 104, 94)
        behind = QRect(21, 24, 166, 138)
        editor = self.open_editor(selection=QRect(), windows=((front, "Front"), (behind, "Behind")))
        completed = []
        editor.completed.connect(lambda *args: completed.append(args))
        window = editor.windowHandle()
        point = QPoint(72, 78)
        QTest.mouseMove(window, point)
        self.assertEqual(editor.hover_index, 0)
        QTest.keyClick(window, Qt.Key.Key_Tab)
        self.assertEqual(editor.hover_index, 1)
        QTest.keyClick(window, Qt.Key.Key_Tab, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(editor.hover_index, 0)
        QTest.wheelEvent(window, point, QPoint(120, 0))
        self.assertEqual(editor.hover_index, 0)
        QTest.wheelEvent(window, point, QPoint(0, -120))
        self.assertEqual(editor.hover_index, 1)
        QTest.keyClick(window, Qt.Key.Key_Return)
        (action, image, rect), = completed
        self.assertEqual((action, rect), ("copy", behind))
        self.assertEqual(image.size(), behind.size())

    def test_direction_keys_move_and_resize_then_enter_confirms_keyboard_selection(self):
        original = QRect(44, 42, 144, 110)
        editor = self.open_editor(selection=original)
        completed = []
        editor.completed.connect(lambda *args: completed.append(args))
        window = editor.windowHandle()
        control, shift, none = Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.ShiftModifier, Qt.KeyboardModifier.NoModifier
        cases = (
            (Qt.Key.Key_Right, none, QRect(45, 42, 144, 110)),
            (Qt.Key.Key_Down, control, QRect(45, 52, 144, 110)),
            (Qt.Key.Key_Right, shift, QRect(45, 52, 145, 110)),
            (Qt.Key.Key_Down, control | shift, QRect(45, 52, 145, 120)),
        )
        for key, modifier, expected in cases:
            QTest.keyClick(window, key, modifier)
            self.assertEqual(editor.selected_geometry(), expected)
        start, end = QPoint(72, 63), QPoint(164, 119)
        QTest.mouseMove(window, start)
        QTest.keyClick(window, Qt.Key.Key_Space)
        QTest.mouseMove(window, end)
        QTest.keyClick(window, Qt.Key.Key_Return)
        self.assertEqual(completed, [])
        self.assertEqual(editor.selected_geometry(), QRect(start, end))
        QTest.keyClick(window, Qt.Key.Key_Enter, Qt.KeyboardModifier.KeypadModifier)
        (action, _, rect), = completed
        self.assertEqual((action, rect), ("copy", QRect(start, end)))

    def test_marquee_and_shift_wheel_only_change_selected_annotations(self):
        editor = self.open_editor()
        for first, last in ((QPoint(53, 62), QPoint(95, 105)), (QPoint(120, 72), QPoint(153, 108)),
                            (QPoint(195, 67), QPoint(217, 103))):
            self.click(editor.tool_buttons["rectangle"])
            self.drag(editor, first, last)
        first, second, third = editor.document.annotations
        selection = editor.selected_geometry()
        self.click(editor.tool_buttons["select"])
        window = editor.windowHandle()
        shift = Qt.KeyboardModifier.ShiftModifier
        QTest.mousePress(window, Qt.MouseButton.LeftButton, shift, pos=QPoint(43, 51))
        QTest.mouseMove(window, QPoint(174, 121))
        QTest.mouseRelease(window, Qt.MouseButton.LeftButton, shift, pos=QPoint(174, 121))
        self.assertEqual(editor.selected_items, {0, 1})
        self.assertFalse(editor.stroke_width.isEnabled())

        point = QPoint(82, 85)
        QTest.wheelEvent(window, point, QPoint(120, 0), stateKey=shift)
        self.assertEqual(editor.document.annotations, (first, second, third))
        QTest.wheelEvent(window, point, QPoint(0, 120), stateKey=shift)
        a, b, c = editor.document.annotations
        self.assertGreater(a.bounds().width(), first.bounds().width())
        self.assertGreater(b.bounds().width(), second.bounds().width())
        self.assertEqual(c, third)
        self.assertEqual(editor.selected_geometry(), selection)
        QTest.keyClick(window, Qt.Key.Key_Delete)
        self.assertEqual(editor.document.annotations, (third,))
        QTest.keyClick(window, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(editor.document.annotations, (a, b, third))

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

    def test_clicks_on_toolbar_padding_do_not_reach_the_canvas(self):
        selection = self.qt.primaryScreen().availableGeometry()
        editor = self.open_editor(selection=selection)
        completed, cancelled = [], []
        editor.completed.connect(lambda *args: completed.append(args))
        editor.cancelled.connect(lambda: cancelled.append(True))
        spin = editor.stroke_width
        for widget, local in ((spin, QPoint(2, 1)), (editor.toolbar, QPoint(3, 3))):
            position = editor.mapFromGlobal(widget.mapToGlobal(local))
            self.assertTrue(editor.selection.contains(position))
            self.assertIs(editor.childAt(position), widget)
            handle = editor.windowHandle()
            QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=position)
            self.assertEqual(editor.drag_mode, "")
            self.assertTrue(editor.toolbar.isVisible())
            QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=position)
            QTest.mouseDClick(handle, Qt.MouseButton.LeftButton, pos=position)
            self.qt.processEvents()
            self.assertTrue(editor.isVisible())
            self.assertEqual(editor.selected_geometry(), selection)
            self.assertEqual(editor.document.annotations, ())
            self.assertEqual(completed, [])
            self.assertEqual(cancelled, [])

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

    def test_escape_cancels_active_selection_gestures_before_closing(self):
        editor = self.open_editor()
        window = editor.windowHandle()
        original = editor.selected_geometry()
        start, end = QPoint(126, 76), QPoint(193, 132)
        QTest.mouseMove(window, start)
        QTest.keyClick(window, Qt.Key.Key_Space)
        QTest.mouseMove(window, end)
        self.assertEqual(editor.selected_geometry(), QRect(editor.mapToGlobal(start), editor.mapToGlobal(end)))
        QTest.keyClick(window, Qt.Key.Key_Escape)
        self.assertEqual(editor.selected_geometry(), original)
        self.assertTrue(editor.toolbar.isVisible())

        edge = editor.selection.topLeft()
        QTest.mousePress(window, Qt.MouseButton.LeftButton, pos=edge)
        QTest.mouseMove(window, edge + QPoint(20, 20))
        self.assertNotEqual(editor.selected_geometry(), original)
        QTest.keyClick(window, Qt.Key.Key_Escape)
        QTest.mouseRelease(window, Qt.MouseButton.LeftButton, pos=edge + QPoint(20, 20))
        self.assertEqual(editor.selected_geometry(), original)
        self.assertTrue(editor.toolbar.isVisible())
        QTest.keyClick(window, Qt.Key.Key_Escape)
        self.assertFalse(editor.isVisible())

    def test_region_history_starts_at_latest_then_moves_to_older_regions(self):
        history = [QRect(34, 50, 110, 80), QRect(90, 60, 140, 120), QRect(175, 100, 170, 130)]
        editor = self.open_editor(history=history)
        window = editor.windowHandle()
        original = editor.selected_geometry()
        QTest.keyClick(window, Qt.Key.Key_Period)
        self.assertEqual(editor.selected_geometry(), original)
        for expected in (*history, history[-1]):
            QTest.keyClick(window, Qt.Key.Key_Comma)
            self.assertEqual(editor.selected_geometry(), expected)
        for expected in (history[1], history[0], history[0]):
            QTest.keyClick(window, Qt.Key.Key_Period)
            self.assertEqual(editor.selected_geometry(), expected)

    def test_redaction_stays_on_original_pixels_when_selection_is_reframed(self):
        editor = self.open_editor()
        window = editor.windowHandle()
        self.click(editor.tool_buttons["redact"])
        with patch.object(QColorDialog, "getColor", return_value=QColor("#000000")) as choose:
            self.click(editor.color_button)
        self.assertFalse(choose.call_args.args[-1] & QColorDialog.ColorDialogOption.ShowAlphaChannel)
        self.drag(editor, QPoint(95, 75), QPoint(145, 135))
        sample = editor.mapToGlobal(QPoint(120, 101))

        def color_at_original_position():
            return editor.export_image().pixelColor(sample - editor.selected_geometry().topLeft())

        self.assertEqual(color_at_original_position(), QColor("#000000"))
        self.click(editor.tool_buttons["select"])
        edge = QPoint(editor.selection.left(), editor.selection.center().y())
        self.drag(editor, edge, edge + QPoint(43, 0))
        self.assertEqual(editor.selection.left(), edge.x() + 43)
        self.assertEqual(color_at_original_position(), QColor("#000000"))
        QTest.keyClick(window, Qt.Key.Key_Right, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(color_at_original_position(), QColor("#000000"))
        self.click(editor.undo_button)
        self.assertEqual(color_at_original_position(), QColor("#ffffff"))
        self.click(editor.redo_button)
        self.assertEqual(color_at_original_position(), QColor("#000000"))

    def test_locked_ratio_holds_when_drawing_or_resizing_against_screen_edge(self):
        editor = self.open_editor(QRect(0, 0, 550, 400), aspect_ratio=2)
        self.drag(editor, QPoint(340, 155), QPoint(549, 390))
        self.assertEqual(editor.selection, QRect(340, 155, 210, 105))
        edge = editor.selection.bottomRight()
        self.drag(editor, edge, edge + QPoint(0, 38))
        self.assertEqual(editor.selection, QRect(340, 155, 210, 105))

    def test_redo_uses_configured_shortcut_instead_of_default_alternate(self):
        editor = self.open_editor(shortcuts={"redo": "Ctrl+J"})
        self.click(editor.tool_buttons["rectangle"])
        self.drag(editor, QPoint(43, 52), QPoint(85, 93))
        first = editor.document.annotations
        self.click(editor.undo_button)
        QTest.keyClick(editor.windowHandle(), Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
        self.assertFalse(editor.document.annotations)
        QTest.keyClick(editor.windowHandle(), Qt.Key.Key_J, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(editor.document.annotations, first)

    def test_reselecting_polyline_does_not_finish_the_current_draft(self):
        editor = self.open_editor()
        line = editor.tool_buttons["line"]
        self.click(line)
        for point in (QPoint(101, 68), QPoint(127, 98)):
            self.click(editor, point)
        self.click(line)
        self.assertTrue(line.isChecked())
        self.assertEqual(len(editor.line_points), 2)
        self.assertFalse(editor.document.annotations)
        QTest.keyClick(editor.windowHandle(), Qt.Key.Key_Return)
        self.assertEqual(len(editor.document.annotations), 1)
        self.assertFalse(editor.line_points)

    def test_undo_and_redo_of_a_number_restore_the_next_step_number(self):
        editor = self.open_editor()
        self.click(editor.tool_buttons["number"])
        self.type_number(editor.number_spin, 12)
        self.click(editor, QPoint(100, 90))
        self.assertEqual(editor.number_spin.value(), 13)
        self.click(editor.undo_button)
        self.assertEqual(editor.number_spin.value(), 12)
        self.click(editor.redo_button)
        self.assertEqual(editor.number_spin.value(), 13)
        self.assertEqual(editor.document.annotations[0].text, "12")

    def test_disabled_capture_modes_have_no_refresh_or_scroll_entry(self):
        editor = self.open_editor(allow_refresh=False, allow_scroll=False)
        refreshed = []
        editor.refresh_requested.connect(lambda: refreshed.append(True))
        QTest.keyClick(editor.windowHandle(), Qt.Key.Key_F5)
        self.assertEqual(refreshed, [])
        more = next(button for button in editor.toolbar.findChildren(QToolButton) if button.text() == "更多")
        labels = {action.text() for action in more.menu().actions()}
        self.assertNotIn("刷新截图", labels)
        self.assertNotIn("滚动截图", labels)

    def test_context_menu_actions_follow_the_clicked_annotation(self):
        editor = self.open_editor()
        self.click(editor.tool_buttons["pen"])
        self.drag(editor, QPoint(65, 70), QPoint(100, 90))

        def actions_at(point):
            labels = []

            def dismiss():
                menu = self.qt.activePopupWidget()
                if isinstance(menu, QMenu):
                    labels.extend(action.text() for action in menu.actions())
                    menu.hide()

            QTimer.singleShot(0, dismiss)
            QTest.mouseClick(editor.windowHandle(), Qt.MouseButton.RightButton, pos=point)
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.assertFalse(editor.findChildren(QMenu, options=Qt.FindChildOption.FindDirectChildrenOnly))
            return labels

        marked = actions_at(QPoint(78, 79))
        self.assertIn("删除所选标注", marked)
        self.assertNotIn("向左旋转所选", marked)
        blank = actions_at(QPoint(200, 120))
        self.assertIn("清空全部标注", blank)
        self.assertNotIn("删除所选标注", blank)

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
