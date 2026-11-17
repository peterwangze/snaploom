"""Opt-in integration with the Windows clipboard and iCode-TUI 0.35.2.

Set SNAPLOOM_TEST_DESKTOP=1 and SNAPLOOM_ICODE_PYTHON to the Python 3.14
interpreter with iCode-TUI==0.35.2 installed, then run this file with pytest.
"""

import asyncio
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import monotonic
import unittest
from unittest.mock import patch


async def verify_icode_paste(source, attachments):
    import ctypes
    from ctypes import wintypes
    from importlib.metadata import version

    from chrys.app.tui.theme import TuiVariableDefaultsMixin
    from chrys.app.tui.widgets.chrome.input_bar import InputBar
    from chrys.foundation.patches.textual_win_sleep import apply_runtime_patch
    from PIL import Image, ImageGrab
    from textual._xterm_parser import XTermParser
    from textual.app import App
    from textual.events import Paste

    assert version("iCode-TUI") == "0.35.2"
    apply_runtime_patch()

    user = ctypes.WinDLL("user32", use_last_error=True)
    has_format = user.IsClipboardFormatAvailable
    has_format.argtypes = (wintypes.UINT,)
    has_format.restype = wintypes.BOOL
    for format_id in (1, 7, 13, 15):  # CF_TEXT, CF_OEMTEXT, CF_UNICODETEXT, CF_HDROP
        assert not has_format(format_id), f"Unexpected native file or text clipboard format {format_id}"
    assert has_format(8), "CF_DIB bitmap format is missing"

    with Image.open(source) as saved:
        expected_pixels = saved.convert("RGBA").tobytes()
        expected_size = saved.size
    grabbed = ImageGrab.grabclipboard()
    assert isinstance(grabbed, Image.Image), "Windows clipboard did not return an image"
    with grabbed:
        assert grabbed.format == "PNG", grabbed.format
        assert grabbed.size == expected_size
        assert grabbed.convert("RGBA").tobytes() == expected_pixels

    class ComposerApp(TuiVariableDefaultsMixin, App):
        def compose(self):
            yield InputBar()

    for route in ("ctrl+v", "shift+insert", "terminal-bracketed-paste"):
        directory = attachments / route
        async with ComposerApp().run_test() as pilot:
            input_bar = pilot.app.query_one(InputBar)
            input_bar.set_clipboard_image_dir(directory)
            composer = input_bar.query_one("#chat-input")
            composer.focus()
            await pilot.pause()
            if route == "terminal-bracketed-paste":
                # Feed the actual terminal sequence through Textual's parser.
                event, = XTermParser().feed("\x1b[200~\x1b[201~")
                assert isinstance(event, Paste) and event.text == ""
                composer.post_message(event)
            else:
                await pilot.press(route)
            await pilot.pause()
            entry = input_bar.draft().references.get("image", 1)
            assert entry is not None, (route, input_bar.value)
            assert input_bar.value == f"{entry.label} ", (route, input_bar.value)
            attached = Path(entry.content).resolve()
            assert attached.is_relative_to(directory.resolve()), (route, attached)
            with Image.open(attached) as image:
                assert image.size == expected_size, (route, image.size)
                assert image.convert("RGBA").tobytes() == expected_pixels, route


@unittest.skipUnless(os.name == "nt" and os.environ.get("SNAPLOOM_TEST_DESKTOP") == "1"
                     and os.environ.get("SNAPLOOM_ICODE_PYTHON"), "requires an opted-in Windows desktop and iCode interpreter")
class ICodeClipboardTests(unittest.TestCase):
    def test_save_and_copy_pastes_the_saved_image_in_real_icode_input(self):
        from PySide6.QtCore import QCoreApplication, QEvent, QProcess, Qt
        from PySide6.QtGui import QColor, QImage
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication, QToolButton

        from snaploom.app import Application
        from snaploom.capture import Snapshot

        qt = QApplication.instance() or QApplication([])
        if qt.platformName() != "windows":
            self.skipTest("requires the native Windows clipboard")

        source = QImage(800, 520, QImage.Format.Format_ARGB32_Premultiplied)
        source.fill(QColor("#245891"))
        source.setPixelColor(2, 3, QColor(32, 96, 168, 117))
        source.setPixelColor(25, 20, QColor(0, 0, 0, 0))
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            app = Application(qt, folder / "profile", testing=True)
            app.workspace.set("save_folder", str(folder))
            app.workspace.set("save_pattern", "Screen capture")
            try:
                for route in ("toolbar", "enter"):
                    with self.subTest(route=route), (
                        patch("snaploom.capture.capture_desktop", return_value=Snapshot(source, source.rect())),
                        patch("snaploom.capture.detect_windows", return_value=[]),
                    ):
                        app.capture({"full": True})
                        deadline = monotonic() + 5
                        while app.editor is None and monotonic() < deadline:
                            QTest.qWait(20)
                        editor = app.editor
                        self.assertIsNotNone(editor)
                        if route == "toolbar":
                            button = editor.toolbar.findChild(QToolButton, "copyCapture")
                            self.assertIsNotNone(button)
                            self.assertTrue(button.isVisible())
                            QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                        else:
                            QTest.keyClick(editor.windowHandle(), Qt.Key.Key_Return)

                        self.assertIsNone(app.editor, app.dashboard.status.text())
                        saved = app.last_saved_file
                        self.assertTrue(saved and saved.is_file())
                        self.check_icode(saved, folder / route, QProcess, QTest)
            finally:
                if qt.clipboard().ownsClipboard():
                    qt.clipboard().clear()
                if app.editor is not None:
                    app.release_capture(restore_dashboard=False)
                app.dashboard.deleteLater()
                app.shutdown()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def check_icode(self, saved, attachments, process_type, qt_test):
        child = process_type()
        try:
            child.start(os.environ["SNAPLOOM_ICODE_PYTHON"], [str(Path(__file__).resolve()), str(saved), str(attachments)])
            self.assertTrue(child.waitForStarted(5000), child.errorString())
            deadline = monotonic() + 30
            while child.state() != process_type.ProcessState.NotRunning and monotonic() < deadline:
                qt_test.qWait(20)  # The clipboard owner may need to answer platform requests.
            self.assertEqual(child.state(), process_type.ProcessState.NotRunning, "iCode paste check timed out")
            stderr = bytes(child.readAllStandardError()).decode("utf-8", errors="replace")
            self.assertEqual(child.exitStatus(), process_type.ExitStatus.NormalExit, stderr)
            self.assertEqual(child.exitCode(), 0, stderr)
        finally:
            if child.state() != process_type.ProcessState.NotRunning:
                child.kill()
                child.waitForFinished(2000)


if __name__ == "__main__":
    asyncio.run(verify_icode_paste(Path(sys.argv[1]), Path(sys.argv[2])))
