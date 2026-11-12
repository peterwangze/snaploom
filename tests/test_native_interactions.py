"""Verify capture editor interactions on the live desktop."""

import os
import unittest

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QRect, QSize, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyle, QStyleFactory, QStyleOptionSpinBox

from snaploom.capture import ScreenFrame, Snapshot
from snaploom.editor import CaptureEditor, ImageEditor
from snaploom.hotkeys import LABELS, Hotkeys


@unittest.skipUnless(os.environ.get("SNAPLOOM_TEST_DESKTOP") == "1", "needs a live test desktop")
class DesktopSelectionTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "requires Win32 global shortcut registration")
    def test_each_windows_global_shortcut_dispatches_and_releases_its_registration(self):
        import ctypes
        from ctypes import wintypes

        app = QApplication.instance() or QApplication([])
        user = ctypes.WinDLL("user32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentThreadId.restype = wintypes.DWORD
        post = user.PostThreadMessageW
        post.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        post.restype = wintypes.BOOL
        thread_id = kernel.GetCurrentThreadId()
        message, modifiers, first_key = 0x0312, 0x0003, 0x7C  # WM_HOTKEY, Ctrl+Alt, F13
        actions = ("capture", "paste", "toggle_pins", "repeat", "click_through", "active")
        self.assertEqual(set(actions), set(LABELS))
        shortcuts = {action: f"Ctrl+Alt+F{index}" for index, action in enumerate(actions, 13)}
        backend = Hotkeys()
        triggered = []
        backend.triggered.connect(triggered.append)
        self.addCleanup(backend.close)
        self.assertEqual(backend.configure(shortcuts), [], backend.errors)
        for index, action in enumerate(actions):
            key = first_key + index
            self.assertTrue(post(thread_id, message, index + 1, (key << 16) | modifiers))
            for _ in range(20):
                QTest.qWait(10)
                if len(triggered) > index:
                    break
            self.assertEqual(triggered, list(actions[:index + 1]), action)

        backend.close()
        self.assertTrue(post(thread_id, message, 1, (first_key << 16) | modifiers))
        app.processEvents()
        self.assertEqual(triggered, list(actions))
        self.assertEqual(backend.configure(shortcuts), [], backend.errors)

    @unittest.skipUnless(os.name == "nt", "exercises native Windows dialog focus")
    def test_enter_on_embedded_tool_button_does_not_apply_the_image(self):
        app = QApplication.instance() or QApplication([])
        image = QImage(320, 220, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
        dialog = ImageEditor(image)
        self.addCleanup(dialog.close)
        applied = []
        dialog.applied.connect(applied.append)
        dialog.show()
        self.assertTrue(QTest.qWaitForWindowExposed(dialog, 2000))
        dialog.activateWindow()
        dialog.canvas.setFocus()
        target = dialog.canvas.tool_buttons["rectangle"]
        for _ in range(30):
            QTest.keyClick(dialog.windowHandle(), Qt.Key.Key_Tab)
            app.processEvents()
            if app.focusWidget() is target:
                break
        self.assertIs(app.focusWidget(), target)
        QTest.keyClick(dialog.windowHandle(), Qt.Key.Key_Return)
        self.assertTrue(dialog.isVisible())
        self.assertEqual(dialog.canvas.tool, "rectangle")
        self.assertEqual(applied, [])

    def test_toolbar_uses_normal_pointer_on_the_desktop(self):
        app = QApplication.instance() or QApplication([])
        available = app.primaryScreen().availableGeometry()
        geometry = QRect(QPoint(), QSize(760, 520).boundedTo(available.size()))
        geometry.moveCenter(available.center())
        source = QImage(geometry.size(), QImage.Format.Format_RGB32)
        source.fill(Qt.GlobalColor.white)
        selection = QRect(geometry.topLeft() + QPoint(24, 40), QSize(245, 178))
        editor = CaptureEditor(Snapshot(source, geometry), selection=selection)
        arrow, text, cross = Qt.CursorShape.ArrowCursor, Qt.CursorShape.IBeamCursor, Qt.CursorShape.CrossCursor

        def assert_desktop_cursor(widget, expected, point=None):
            position = widget.mapToGlobal(widget.rect().center() if point is None else point)
            window = widget.window()
            local = window.mapFromGlobal(position)
            handle = window.windowHandle()
            QTest.mouseMove(handle, local)
            hit = window.childAt(local) or window
            self.assertIs(hit, widget)
            self.assertEqual(handle.cursor().shape(), expected)

        try:
            editor.show()
            self.assertTrue(QTest.qWaitForWindowExposed(editor, 2000))
            canvas_point = QPoint(100, 90)
            assert_desktop_cursor(editor, cross, canvas_point)
            assert_desktop_cursor(editor.toolbar, arrow, QPoint(4, 4))
            assert_desktop_cursor(editor.tool_buttons["pen"], arrow)
            assert_desktop_cursor(editor.stroke_width.lineEdit(), text)
            assert_desktop_cursor(editor, cross, canvas_point)
        finally:
            editor.close()

    def test_editor_accepts_width_input_and_undoes_one_change_on_the_desktop(self):
        app = QApplication.instance() or QApplication([])
        available = app.primaryScreen().availableGeometry()
        geometry = QRect(QPoint(), QSize(740, 500).boundedTo(available.size()))
        geometry.moveCenter(available.center())
        source = QImage(geometry.size(), QImage.Format.Format_RGB32)
        source.fill(Qt.GlobalColor.white)
        selection = QRect(geometry.topLeft() + QPoint(24, 40), QSize(245, 178))
        editor = CaptureEditor(Snapshot(source, geometry), selection=selection)
        completed = []
        editor.completed.connect(lambda *values: completed.append(values))

        def click(widget, point=None):
            point = widget.rect().center() if point is None else point
            window = widget.window()
            QTest.mouseClick(window.windowHandle(), Qt.MouseButton.LeftButton,
                             pos=window.mapFromGlobal(widget.mapToGlobal(point)))
            app.processEvents()

        def type_width(value):
            field = editor.stroke_width.lineEdit()
            click(field)
            window = field.window().windowHandle()
            QTest.keyClick(window, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
            for digit in str(value):
                QTest.keyClick(window, ord(digit))
            QTest.keyClick(window, Qt.Key.Key_Return)
            app.processEvents()
            self.assertTrue(editor.isVisible())
            self.assertEqual(completed, [])

        try:
            editor.show()
            self.assertTrue(QTest.qWaitForWindowExposed(editor, 2000))
            click(editor.tool_buttons["pen"])
            type_width(12)
            handle = editor.windowHandle()
            start, end = QPoint(65, 100), QPoint(185, 100)
            QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=start)
            QTest.mouseMove(handle, end)
            QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=end)
            self.assertEqual(editor.document.annotations[0].width, 12)
            click(editor.tool_buttons["select"])
            click(editor, QPoint(120, 100))
            type_width(24)
            self.assertEqual(editor.document.annotations[0].width, 24)
            click(editor.undo_button)
            self.assertEqual(editor.document.annotations[0].width, 12)
            click(editor.redo_button)
            self.assertEqual(editor.document.annotations[0].width, 24)
        finally:
            editor.close()

    @unittest.skipUnless(os.name == "nt", "exercises the Windows 11 spin box style")
    def test_stroke_width_buttons_preserve_capture_on_the_desktop(self):
        app = QApplication.instance() or QApplication([])
        if "windows11" not in {name.lower() for name in QStyleFactory.keys()}:
            self.skipTest("Qt Windows 11 style is unavailable")
        self.addCleanup(app.setStyle, app.style().objectName())
        app.setStyle("windows11")
        available = app.primaryScreen().availableGeometry()

        def click(widget, point=None, *, double=False):
            window = widget.window()
            point = widget.rect().center() if point is None else point
            local = window.mapFromGlobal(widget.mapToGlobal(point))
            mouse_click = QTest.mouseDClick if double else QTest.mouseClick
            mouse_click(window.windowHandle(), Qt.MouseButton.LeftButton, pos=local)
            app.processEvents()

        def step_points(spin):
            option = QStyleOptionSpinBox()
            spin.initStyleOption(option)
            points = []
            for control in (QStyle.SubControl.SC_SpinBoxUp, QStyle.SubControl.SC_SpinBoxDown):
                rect = spin.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, option, control, spin)
                self.assertTrue(rect.isValid())
                points.append(rect.center())
            return points

        for size, floating in ((QSize(740, 500), False), (QSize(200, 158), True)):
            with self.subTest(floating=floating):
                geometry = QRect(QPoint(), size.boundedTo(available.size()))
                geometry.moveCenter(available.center())
                source = QImage(geometry.size(), QImage.Format.Format_RGB32)
                source.fill(Qt.GlobalColor.white)
                selection = QRect(geometry.topLeft() + QPoint(24, 30), QSize(158, 110))
                editor = CaptureEditor(Snapshot(source, geometry), selection=selection, options={"locale": "zh-CN"})
                cancelled, completed = [], []
                editor.cancelled.connect(lambda: cancelled.append(True))
                editor.completed.connect(lambda *values: completed.append(values))
                try:
                    editor.show()
                    self.assertTrue(QTest.qWaitForWindowExposed(editor, 2000))
                    self.assertEqual(editor.toolbar.isWindow(), floating)
                    spin = editor.stroke_width
                    initial = spin.value()
                    up, down = step_points(spin)
                    for point, expected in ((up, initial + 1), (up, initial + 2),
                                            (down, initial + 1), (down, initial)):
                        click(spin, point)
                        self.assertEqual(spin.value(), expected)
                        self.assertEqual(editor.selected_geometry(), selection)
                        self.assertTrue(editor.isVisible())
                    self.assertFalse(editor.document.annotations)

                    click(editor.tool_buttons["pen"])
                    handle = editor.windowHandle()
                    start, end = QPoint(48, 62), QPoint(125, 91)
                    QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=start)
                    QTest.mouseMove(handle, end)
                    QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=end)
                    original, = editor.document.annotations
                    click(editor.tool_buttons["select"])
                    click(editor, QPoint(87, 77))
                    self.assertEqual(editor.selected_items, {0})
                    up, down = step_points(spin)
                    for point, expected in ((up, original.width + 1), (down, original.width)):
                        click(spin, point)
                        updated, = editor.document.annotations
                        self.assertEqual(spin.value(), expected)
                        self.assertEqual(updated.width, expected)
                        self.assertEqual(updated.points, original.points)
                        self.assertEqual(editor.selected_items, {0})
                        self.assertEqual(editor.selected_geometry(), selection)
                        self.assertTrue(editor.isVisible())

                    if not floating:
                        editor._set_selection(editor.rect())
                        app.processEvents()
                        up, _ = step_points(spin)
                        spin.setEnabled(False)
                        click(spin, up, double=True)
                        click(editor.toolbar, QPoint(3, 3), double=True)
                        self.assertEqual(editor.selected_geometry(), geometry)
                        self.assertEqual(editor.drag_mode, "")
                        self.assertTrue(editor.toolbar.isVisible())
                    self.assertEqual(cancelled, [])
                    self.assertEqual(completed, [])
                finally:
                    editor.close()

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
