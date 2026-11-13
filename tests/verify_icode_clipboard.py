import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from unittest.mock import patch

from PySide6.QtCore import QProcess, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolButton

from snaploom.app import Application
from snaploom.capture import Snapshot


reader = """
import asyncio
import ctypes as C
from ctypes import wintypes as W
from io import BytesIO
import json
import sys
from contextlib import contextmanager
from importlib.metadata import version
from pathlib import Path

from PIL import Image, ImageGrab
from textual.app import App, ComposeResult
from textual.events import Paste

from chrys.app.tui.theme import TuiVariableDefaultsMixin
from chrys.app.tui.widgets.chrome.input_bar import InputBar, _ChatTextArea
from chrys.foundation.patches.textual_win_sleep import apply_runtime_patch

apply_runtime_patch()


class ComposerApp(TuiVariableDefaultsMixin, App):
    def compose(self) -> ComposeResult:
        yield InputBar()


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


@contextmanager
def open_clipboard():
    if not user.OpenClipboard(None):
        raise C.WinError(C.get_last_error())
    try:
        yield
    finally:
        user.CloseClipboard()


def native_clipboard_data(format_id: int) -> bytes:
    handle = user.GetClipboardData(format_id)
    assert handle, f"Clipboard format {format_id} unavailable"
    pointer = kernel.GlobalLock(handle)
    if not pointer:
        raise C.WinError(C.get_last_error())
    try:
        return C.string_at(pointer, kernel.GlobalSize(handle))
    finally:
        kernel.GlobalUnlock(handle)


async def paste_into_composer(attachment_directory: Path, shortcut: str, source_file: Path | None, path_text: str) -> dict:
    async with ComposerApp().run_test() as pilot:
        input_bar = pilot.app.query_one(InputBar)
        input_bar.set_clipboard_image_dir(attachment_directory)
        text_area = input_bar.query_one("#chat-input", _ChatTextArea)
        text_area.focus()
        await pilot.pause()
        assert text_area.has_focus
        if shortcut == "terminal-paste":
            text_area.post_message(Paste(""))
        elif shortcut == "terminal-path":
            text_area.post_message(Paste(path_text))
        else:
            await pilot.press(shortcut)
        await pilot.pause()
        entry = input_bar.draft().references.get("image", 1)
        assert entry is not None, (shortcut, input_bar.value)
        assert input_bar.value == f"{entry.label} ", (shortcut, input_bar.value, entry.label)
        saved = Path(entry.content)
        assert saved.is_file(), saved
        if source_file is None:
            assert saved.resolve().is_relative_to(attachment_directory.resolve()), saved
        else:
            assert saved.resolve() == source_file.resolve(), (shortcut, saved, source_file)
        with Image.open(saved) as attachment:
            return {"size": attachment.size, "pixel": attachment.convert("RGBA").getpixel((2, 3))}


assert version("iCode-TUI") == "0.35.2"
directory = Path(sys.argv[1])
source_file = Path(sys.argv[2]) if len(sys.argv) > 2 else None
path_text = ""
if source_file is not None:
    with open_clipboard():
        path_text = native_clipboard_data(13).decode("utf-16-le", errors="replace").partition("\\0")[0]  # CF_UNICODETEXT
    assert path_text == str(source_file), (path_text, source_file)
clipboard = ImageGrab.grabclipboard()
if source_file is None:
    clipboard_format = clipboard.format
    clipboard.close()
else:
    assert isinstance(clipboard, list) and [Path(path).resolve() for path in clipboard] == [source_file.resolve()]
    clipboard_format = "CF_HDROP"
    # A graphics application can request the PNG even when file list has priority.
    # Read the standard registered image through Win32 rather than the CF_HDROP-first Pillow path.
    png = user.RegisterClipboardFormatW("PNG")
    with open_clipboard():
        assert user.IsClipboardFormatAvailable(8), "CF_DIB unavailable"
        payload = native_clipboard_data(png)
    with Image.open(BytesIO(payload), formats=["PNG"]) as preview, Image.open(source_file) as saved:
        assert preview.size == saved.size
        assert preview.convert("RGBA").getpixel((2, 3)) == saved.convert("RGBA").getpixel((2, 3))
shortcuts = ("ctrl+v", "shift+insert", "terminal-paste")
if source_file is not None:
    shortcuts += ("terminal-path",)
results = {}
for shortcut in shortcuts:
    try:
        results[shortcut] = asyncio.run(paste_into_composer(directory / shortcut, shortcut, source_file, path_text))
    except AssertionError as error:
        results[shortcut] = {"error": str(error)}
print(json.dumps({"format": clipboard_format, "shortcuts": results}))
"""

qt = QApplication([])
with TemporaryDirectory(prefix="snaploom-icode-verify-") as temp:
    root = Path(temp)
    app = Application(qt, root / "profile", testing=True)
    failures = []
    try:
        app.workspace.set("save_folder", str(root))
        app.workspace.set("save_pattern", "Snaploom 截图 测试")
        for format_ in (QImage.Format.Format_RGB32, QImage.Format.Format_ARGB32_Premultiplied):
            fixture = QImage(800, 520, format_)
            fixture.fill(QColor("white"))
            fixture.setPixelColor(2, 3, QColor(32, 96, 168, 117))
            expected = list(fixture.pixelColor(2, 3).getRgb())
            for mode in ("PNG first", "Toolbar Save and Copy", "Editor Enter"):
                source_file = None
                if mode == "PNG first":
                    app.copy_image(fixture)
                else:
                    # Exercise the real editor controls with a deterministic captured frame.
                    with (
                        patch("snaploom.capture.capture_desktop", return_value=Snapshot(fixture, fixture.rect())),
                        patch("snaploom.capture.detect_windows", return_value=[]),
                    ):
                        app.capture({"full": True})
                        deadline = monotonic() + 3
                        while app.editor is None and monotonic() < deadline:
                            QTest.qWait(20)
                    editor = app.editor
                    assert editor is not None and editor.isVisible()
                    if mode == "Editor Enter":
                        QTest.keyClick(editor.windowHandle(), Qt.Key.Key_Return)
                    else:
                        button = editor.toolbar.findChild(QToolButton, "copyCapture")
                        assert button is not None and button.isVisible()
                        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                    assert app.editor is None, app.dashboard.status.text()
                    source_file = app.last_saved_file
                    assert source_file is not None and source_file.is_file() and "截图 测试" in source_file.name

                child = QProcess()
                try:
                    arguments = ["-c", reader, str(root / "attachments")]
                    if source_file is not None:
                        arguments.append(str(source_file))
                    child.start(os.environ["ICODE_PYTHON"], arguments)
                    assert child.waitForStarted(5000), child.errorString()
                    deadline = monotonic() + 30
                    while child.state() != QProcess.ProcessState.NotRunning and monotonic() < deadline:
                        QTest.qWait(20)
                    output = bytes(child.readAllStandardOutput()).decode().strip()
                    errors = bytes(child.readAllStandardError()).decode(errors="replace")
                    print(format_.name, mode, child.exitCode(), output, errors)
                    if child.state() != QProcess.ProcessState.NotRunning or child.exitCode() != 0:
                        failures.append((format_.name, mode, errors or "Reader timed out"))
                        continue
                    shortcuts = ("ctrl+v", "shift+insert", "terminal-paste")
                    if source_file is not None:
                        shortcuts += ("terminal-path",)
                    result = json.loads(output)
                    if result != {
                        "format": "CF_HDROP" if source_file else "PNG",
                        "shortcuts": {shortcut: {"size": [800, 520], "pixel": expected} for shortcut in shortcuts},
                    }:
                        failures.append((format_.name, mode, result))
                finally:
                    if child.state() != QProcess.ProcessState.NotRunning:
                        child.kill()
                        child.waitForFinished(2000)
    finally:
        if qt.clipboard().ownsClipboard():
            qt.clipboard().clear()
        app.shutdown()
    assert not failures, failures
