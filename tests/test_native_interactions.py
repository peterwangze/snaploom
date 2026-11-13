"""Verify capture editor interactions on the live desktop."""

import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QProcess, QRect, QSize, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyle, QStyleFactory, QStyleOptionSpinBox

from snaploom.capture import ScreenFrame, Snapshot
from snaploom.editor import CaptureEditor, ImageEditor
from snaploom.hotkeys import LABELS, Hotkeys


@unittest.skipUnless(os.environ.get("SNAPLOOM_TEST_DESKTOP") == "1", "needs a live test desktop")
class DesktopSelectionTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "requires the native Windows image clipboard")
    def test_windows_copy_exposes_native_image_and_saved_file_formats_to_another_process(self):
        from snaploom.app import Application

        qt = QApplication.instance() or QApplication([])
        if qt.platformName() != "windows":
            self.skipTest("requires the native Windows clipboard")

        reader = r"""
import ctypes as C
from ctypes import wintypes as W
from io import BytesIO
import json
import sys
import time

from PIL import Image, ImageGrab

user = C.WinDLL("user32", use_last_error=True)
kernel = C.WinDLL("kernel32", use_last_error=True)
user.OpenClipboard.argtypes = (W.HWND,)
user.RegisterClipboardFormatW.argtypes = (W.LPCWSTR,)
user.RegisterClipboardFormatW.restype = W.UINT
user.GetClipboardData.argtypes = (W.UINT,)
user.GetClipboardData.restype = W.HGLOBAL
user.IsClipboardFormatAvailable.argtypes = (W.UINT,)
kernel.GlobalLock.argtypes = (W.HGLOBAL,)
kernel.GlobalLock.restype = C.c_void_p
kernel.GlobalSize.argtypes = (W.HGLOBAL,)
kernel.GlobalSize.restype = C.c_size_t
kernel.GlobalUnlock.argtypes = (W.HGLOBAL,)

def clipboard_bytes(format_):
    handle = user.GetClipboardData(format_)
    if not handle:
        raise RuntimeError(f"No clipboard data in format {format_}")
    pointer = kernel.GlobalLock(handle)
    if not pointer:
        raise C.WinError(C.get_last_error())
    try:
        return C.string_at(pointer, kernel.GlobalSize(handle))
    finally:
        kernel.GlobalUnlock(handle)

png = user.RegisterClipboardFormatW("PNG")
if not png:
    raise C.WinError(C.get_last_error())
for _ in range(50):
    if user.OpenClipboard(None):
        break
    time.sleep(0.02)
else:
    raise C.WinError(C.get_last_error())
try:
    # Use the registered format exactly as native clipboard readers do.
    payload = clipboard_bytes(png)
    has_dib = bool(user.IsClipboardFormatAvailable(8))  # CF_DIB
    path = None
    if len(sys.argv) > 1:
        path = clipboard_bytes(13).decode("utf-16-le").split("\0", 1)[0]  # CF_UNICODETEXT
finally:
    user.CloseClipboard()

with Image.open(BytesIO(payload), formats=["PNG"]) as source:
    image = source.convert("RGBA")
    grabbed = ImageGrab.grabclipboard()
    if isinstance(grabbed, list):
        print(json.dumps({
            "size": image.size, "pixel": image.getpixel((2, 3)), "has_dib": has_dib,
            "files": grabbed, "text": path,
        }))
    else:
        if not isinstance(grabbed, Image.Image):
            raise RuntimeError("Pillow did not read the Windows clipboard as an image or file list")
        with grabbed:
            print(json.dumps({
                "size": image.size, "pixel": image.getpixel((2, 3)), "has_dib": has_dib,
                "grabbed_format": grabbed.format, "grabbed_size": grabbed.size,
                "grabbed_pixel": grabbed.convert("RGBA").getpixel((2, 3)),
            }))
"""
        with TemporaryDirectory() as directory:
            folder = Path(directory).resolve()
            app = Application(qt, folder, testing=True)
            clipboard = qt.clipboard()
            copied = False
            try:
                app.workspace.set("save_folder", str(folder))
                app.workspace.set("save_pattern", "屏幕 截图")
                for action, format_, color in (
                    ("copy", QImage.Format.Format_RGB32, QColor(35, 88, 159)),
                    ("copy", QImage.Format.Format_ARGB32, QColor(35, 88, 159, 117)),
                    ("copy", QImage.Format.Format_ARGB32_Premultiplied, QColor(35, 88, 159, 117)),
                    ("save-copy", QImage.Format.Format_ARGB32_Premultiplied, QColor(35, 88, 159, 117)),
                ):
                    with self.subTest(action=action, image_format=format_):
                        image = QImage(16, 12, format_)
                        image.fill(QColor("white"))
                        image.setPixelColor(2, 3, color)
                        app.output(image, action, silent=True, record=False)
                        copied = True
                        process = QProcess()
                        try:
                            arguments = ["-c", reader]
                            if action == "save-copy":
                                arguments.append("read-file-path")
                            process.start(sys.executable, arguments)
                            self.assertTrue(process.waitForStarted(5000), process.errorString())
                            deadline = monotonic() + 10
                            while process.state() != QProcess.ProcessState.NotRunning and monotonic() < deadline:
                                QTest.qWait(20)  # Windows may request the data from the owning Qt process.
                            self.assertEqual(process.state(), QProcess.ProcessState.NotRunning, "Clipboard reader timed out")
                            stderr = bytes(process.readAllStandardError()).decode("utf-8", errors="replace")
                            self.assertEqual(process.exitStatus(), QProcess.ExitStatus.NormalExit, stderr)
                            self.assertEqual(process.exitCode(), 0, stderr)
                            data = json.loads(bytes(process.readAllStandardOutput()))
                            self.assertEqual(data["size"], [image.width(), image.height()])
                            self.assertEqual(data["pixel"], list(image.pixelColor(2, 3).getRgb()))
                            self.assertTrue(data["has_dib"])
                            if action == "save-copy":
                                target, = folder.glob("*.png")
                                self.assertEqual([Path(name) for name in data["files"]], [target])
                                self.assertEqual(data["text"], str(target))
                                self.assertEqual(list(QImage(str(target)).pixelColor(2, 3).getRgb()), data["pixel"])
                            else:
                                self.assertEqual(data["grabbed_format"], "PNG")
                                self.assertEqual(data["grabbed_size"], data["size"])
                                self.assertEqual(data["grabbed_pixel"], data["pixel"])
                        finally:
                            if process.state() != QProcess.ProcessState.NotRunning:
                                process.kill()
                                process.waitForFinished(2000)
            finally:
                if copied and clipboard.ownsClipboard():
                    clipboard.clear()
                app.dashboard.deleteLater()
                app.shutdown()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    @unittest.skipUnless(sys.platform == "linux", "exercises Linux file clipboard interoperability")
    def test_copy_file_publishes_both_clipboard_formats_to_another_process(self):
        from snaploom.app import Application

        qt = QApplication.instance() or QApplication([])
        if qt.platformName() != "xcb":
            self.skipTest("requires the native X11 clipboard")

        reader = """
import json
from PySide6.QtGui import QGuiApplication

qt = QGuiApplication([])
mime = qt.clipboard().mimeData()
print(json.dumps({
    "urls": [url.toLocalFile() for url in mime.urls()],
    "gnome": bytes(mime.data("x-special/gnome-copied-files")).decode("ascii"),
}))
"""
        with TemporaryDirectory() as directory:
            folder = Path(directory).resolve()
            app = Application(qt, folder, testing=True)
            process = QProcess()
            try:
                app.workspace.set("save_folder", str(folder))
                app.workspace.set("save_pattern", "截图 示例")
                image = QImage(16, 12, QImage.Format.Format_RGB32)
                image.fill(Qt.GlobalColor.white)
                app.output(image, "copy-file", silent=True, record=False, prompt_file=False)
                target = folder / "截图 示例.png"
                self.assertEqual(QImage(str(target)).size(), image.size())

                process.start(sys.executable, ["-c", reader])
                self.assertTrue(process.waitForStarted(5000), process.errorString())
                deadline = monotonic() + 10
                while process.state() != QProcess.ProcessState.NotRunning and monotonic() < deadline:
                    QTest.qWait(20)  # The clipboard owner must process X11 selection requests.
                self.assertEqual(process.state(), QProcess.ProcessState.NotRunning, "Clipboard reader timed out")
                stderr = bytes(process.readAllStandardError()).decode("utf-8", errors="replace")
                self.assertEqual(process.exitStatus(), QProcess.ExitStatus.NormalExit, stderr)
                self.assertEqual(process.exitCode(), 0, stderr)
                data = json.loads(bytes(process.readAllStandardOutput()))
                self.assertEqual(data["urls"], [str(target)])
                self.assertEqual(data["gnome"], "copy\n" + target.as_uri())
            finally:
                if process.state() != QProcess.ProcessState.NotRunning:
                    process.kill()
                    process.waitForFinished(2000)
                qt.clipboard().clear()
                app.dashboard.deleteLater()
                app.shutdown()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

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
