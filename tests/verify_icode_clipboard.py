import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic

from PySide6.QtCore import QMimeData, QProcess, QProcessEnvironment
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from snaploom.app import Application
from snaploom.storage import png_bytes


reader = """
import asyncio
import json
import sys
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


async def paste_into_composer(attachment_directory: Path, shortcut: str, source_file: Path | None) -> dict:
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
            text_area.post_message(Paste(str(source_file)))
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
clipboard = ImageGrab.grabclipboard()
if source_file is None:
    clipboard_format = clipboard.format
    clipboard.close()
else:
    assert isinstance(clipboard, list) and [Path(path).resolve() for path in clipboard] == [source_file.resolve()]
    clipboard_format = "CF_HDROP"
shortcuts = ("ctrl+v", "shift+insert", "terminal-paste")
if source_file is not None:
    shortcuts += ("terminal-path",)
results = {}
for shortcut in shortcuts:
    try:
        results[shortcut] = asyncio.run(paste_into_composer(directory / shortcut, shortcut, source_file))
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
        app.workspace.set("save_pattern", "Snaploom clipboard fixture")
        for format_ in (QImage.Format.Format_RGB32, QImage.Format.Format_ARGB32_Premultiplied):
            fixture = QImage(16, 12, format_)
            fixture.fill(QColor("white"))
            fixture.setPixelColor(2, 3, QColor(32, 96, 168, 117))
            expected = list(fixture.pixelColor(2, 3).getRgb())
            for mode in ("legacy", "PNG first", "CF_HDROP"):
                source_file = None
                if mode == "PNG first":
                    app.copy_image(fixture)
                elif mode == "legacy":
                    mime = QMimeData()
                    mime.setImageData(fixture)
                    mime.setData("image/png", png_bytes(fixture))
                    qt.clipboard().setMimeData(mime)
                else:
                    app.output(fixture, "copy-file", silent=True, record=False, prompt_file=False)
                    source_file = app.last_saved_file
                    assert source_file is not None and source_file.is_file()

                child = QProcess()
                environment = QProcessEnvironment.systemEnvironment()
                child.setProcessEnvironment(environment)
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
                    if mode != "legacy":
                        if child.state() != QProcess.ProcessState.NotRunning or child.exitCode() != 0:
                            failures.append((format_.name, mode, errors or "Reader timed out"))
                            continue
                        shortcuts = ("ctrl+v", "shift+insert", "terminal-paste")
                        if source_file is not None:
                            shortcuts += ("terminal-path",)
                        result = json.loads(output)
                        if result != {
                            "format": "CF_HDROP" if source_file else "PNG",
                            "shortcuts": {shortcut: {"size": [16, 12], "pixel": expected} for shortcut in shortcuts},
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
