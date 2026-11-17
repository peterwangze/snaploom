"""Exercise the real Windows Terminal clipboard route in the isolated CI desktop."""

import ctypes as C
from ctypes import wintypes as W
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import monotonic
from uuid import uuid4


def run_composer(root):
    from chrys.app.tui.theme import TuiVariableDefaultsMixin
    from chrys.app.tui.widgets.chrome.input_bar import InputBar
    from chrys.foundation.patches.textual_win_sleep import apply_runtime_patch
    from textual.app import App

    class ComposerApp(TuiVariableDefaultsMixin, App):
        def compose(self):
            yield InputBar()

        def on_mount(self):
            self.input_bar = self.query_one(InputBar)
            self.input_bar.set_clipboard_image_dir(root / "attachments")
            self.input_bar.query_one("#chat-input").focus()
            self.call_after_refresh(lambda: (root / "ready").touch())
            self.set_interval(0.05, self.check_attachment)

        def check_attachment(self):
            entry = self.input_bar.draft().references.get("image", 1)
            if entry is not None:
                attachment = Path(entry.content).resolve()
                with (root / "attached").open("w", encoding="utf-8") as result:
                    result.write(str(attachment))
                self.exit()

    apply_runtime_patch()
    ComposerApp().run()


def send_paste(modifier, key):
    pointer = C.c_size_t

    class Keyboard(C.Structure):
        _fields_ = [("virtual_key", W.WORD), ("scan", W.WORD), ("flags", W.DWORD),
                    ("time", W.DWORD), ("extra", pointer)]

    class Mouse(C.Structure):
        _fields_ = [("x", W.LONG), ("y", W.LONG), ("data", W.DWORD), ("flags", W.DWORD),
                    ("time", W.DWORD), ("extra", pointer)]

    class EventData(C.Union):
        _fields_ = [("keyboard", Keyboard), ("mouse", Mouse)]

    class Event(C.Structure):
        _fields_ = [("kind", W.DWORD), ("data", EventData)]

    flags = 1 if key == 0x2D else 0  # Insert is an extended key.
    events = (Event * 4)(*[Event(1, EventData(keyboard=Keyboard(code, 0, state, 0, 0))) for code, state in (
        (modifier, 0), (key, flags), (key, flags | 2), (modifier, 2),
    )])
    user = C.WinDLL("user32", use_last_error=True)
    send_input = user.SendInput
    send_input.argtypes = (W.UINT, C.POINTER(Event), C.c_int)
    send_input.restype = W.UINT
    if send_input(len(events), events, C.sizeof(Event)) != len(events):
        raise C.WinError(C.get_last_error())


def foreground_title():
    user = C.WinDLL("user32", use_last_error=True)
    user.GetForegroundWindow.restype = W.HWND
    user.GetWindowTextW.argtypes = (W.HWND, W.LPWSTR, C.c_int)
    title = C.create_unicode_buffer(1024)
    user.GetWindowTextW(user.GetForegroundWindow(), title, len(title))
    return title.value


def verify_terminal():
    from PIL import Image
    from PySide6.QtCore import QProcess
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from snaploom.app import Application

    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise RuntimeError("The physical-key probe requires the isolated hosted CI desktop")
    qt = QApplication([])
    image = QImage(160, 90, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#245891"))
    image.setPixelColor(2, 3, QColor(32, 96, 168, 117))
    image.setPixelColor(30, 20, QColor(0, 0, 0, 0))

    def wait_until(predicate, timeout):
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            if predicate():
                return True
            QTest.qWait(25)
        return False

    with TemporaryDirectory(prefix="snaploom-terminal-") as directory:
        root = Path(directory).resolve()
        app = Application(qt, root / "profile", testing=True)
        app.workspace.set("save_folder", str(root))
        try:
            for route, modifier, key in (("ctrl+v", 0x11, 0x56), ("shift+insert", 0x10, 0x2D)):
                composer = root / route
                composer.mkdir()
                title = f"SnaploomPaste-{uuid4().hex}"
                app.output(image, "save-copy", silent=True, record=False)
                saved = app.last_saved_file
                assert saved.is_file()
                process = QProcess()
                try:
                    process.start(os.environ["SNAPLOOM_WINDOWS_TERMINAL"], [
                        "-w", "new", "new-tab", "--title", title, "--suppressApplicationTitle",
                        os.environ["SNAPLOOM_ICODE_PYTHON"], str(Path(__file__).resolve()), "composer", str(composer),
                    ])
                    assert process.waitForStarted(5000), process.errorString()
                    ready = composer / "ready"
                    error = composer / "error"
                    if not wait_until(lambda: ready.is_file() or error.is_file(), 20):
                        stdout = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")
                        stderr = bytes(process.readAllStandardError()).decode("utf-8", errors="replace")
                        raise RuntimeError(f"{route}: no iCode startup marker. Foreground={foreground_title()!r}; {stdout}; {stderr}")
                    assert not error.exists(), error.read_text(encoding="utf-8") if error.exists() else ""
                    assert wait_until(lambda: title in foreground_title(), 8), foreground_title()
                    send_paste(modifier, key)
                    result = composer / "attached"
                    assert wait_until(result.is_file, 10), f"{route}: no image attachment; foreground={foreground_title()!r}"
                    attached = Path(result.read_text(encoding="utf-8")).resolve()
                    assert attached.is_relative_to(composer / "attachments"), attached
                    with Image.open(saved) as original, Image.open(attached) as actual:
                        assert actual.size == original.size
                        assert actual.convert("RGBA").tobytes() == original.convert("RGBA").tobytes()
                    print(f"{route}: real Windows Terminal delivered the image to iCode with exact RGBA pixels")
                    wait_until(lambda: process.state() == QProcess.ProcessState.NotRunning, 3)
                finally:
                    if process.state() != QProcess.ProcessState.NotRunning:
                        process.kill()
                        process.waitForFinished(2000)
        finally:
            if qt.clipboard().ownsClipboard():
                qt.clipboard().clear()
            app.shutdown()


if __name__ == "__main__":
    if sys.argv[1:] and sys.argv[1] == "composer":
        import traceback

        root = Path(sys.argv[2])
        try:
            run_composer(root)
        except Exception:
            (root / "error").write_text(traceback.format_exc(), encoding="utf-8")
            raise
    else:
        verify_terminal()
