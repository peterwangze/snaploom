"""Measure the actual clipboard on a dedicated Windows test runner."""

import ctypes as C
import json
import os
import subprocess
import sys
from ctypes import wintypes as W
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic


user = C.WinDLL("user32", use_last_error=True)
user.GetClipboardSequenceNumber.restype = W.DWORD
user.OpenClipboard.argtypes = (W.HWND,)
user.EnumClipboardFormats.argtypes = (W.UINT,)
user.EnumClipboardFormats.restype = W.UINT
user.GetClipboardFormatNameW.argtypes = (W.UINT, W.LPWSTR, C.c_int)
user.IsClipboardFormatAvailable.argtypes = (W.UINT,)

COLOR = (36, 88, 134, 255)
STANDARD_FORMATS = {
    1: "CF_TEXT", 2: "CF_BITMAP", 7: "CF_OEMTEXT", 8: "CF_DIB", 13: "CF_UNICODETEXT", 15: "CF_HDROP",
    16: "CF_LOCALE", 17: "CF_DIBV5",
}


def inspect_clipboard(expected_file):
    from PIL import Image, ImageGrab

    for _ in range(25):
        if user.OpenClipboard(None):
            break
        C.windll.kernel32.Sleep(20)
    else:
        raise C.WinError(C.get_last_error())
    formats = []
    try:
        format_id = 0
        while format_id := user.EnumClipboardFormats(format_id):
            name = C.create_unicode_buffer(128)
            user.GetClipboardFormatNameW(format_id, name, len(name))
            formats.append(STANDARD_FORMATS.get(format_id, name.value or f"0x{format_id:x}"))
        availability = {
            "image": bool(user.IsClipboardFormatAvailable(8)),
            "text": bool(user.IsClipboardFormatAvailable(13)),
            "file_drop": bool(user.IsClipboardFormatAvailable(15)),
        }
    finally:
        user.CloseClipboard()

    source = ImageGrab.grabclipboard()
    result = {"formats": formats, "available": availability, "consumer": None}
    if isinstance(source, Image.Image):
        result["consumer"] = source.format
    elif isinstance(source, list):
        exact_fixture = bool(expected_file) and [Path(path).resolve() for path in source] == [Path(expected_file).resolve()]
        result.update(consumer="files", exact_fixture=exact_fixture)
        source = Image.open(expected_file) if exact_fixture else None
    if source is not None:
        with source:
            width, height = source.size
            result.update(size=[width, height], sample=source.convert("RGBA").getpixel((width // 2, height // 2)))
    print(json.dumps(result))


def compare(executable):
    from PySide6.QtCore import QProcess, Qt
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QWidget

    from snaploom.app import Application

    qt = QApplication([])

    def probe(label, expected_file=""):
        process = QProcess()
        try:
            process.start(sys.executable, ["-m", "tests.probe_clipboard_reference", "read", str(expected_file)])
            if not process.waitForStarted(5000):
                raise RuntimeError(process.errorString())
            deadline = monotonic() + 12
            while process.state() != QProcess.ProcessState.NotRunning and monotonic() < deadline:
                QTest.qWait(20)
            if process.state() != QProcess.ProcessState.NotRunning or process.exitCode() != 0:
                raise RuntimeError(bytes(process.readAllStandardError()).decode(errors="replace"))
            result = json.loads(bytes(process.readAllStandardOutput()))
            print(label, json.dumps(result), flush=True)
            assert result.get("sample") == list(COLOR), f"{label} did not provide the synthetic test pixels"
            return result
        finally:
            if process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
                process.waitForFinished(2000)

    with TemporaryDirectory(prefix="clipboard-reference-") as temporary:
        root = Path(temporary).resolve()
        original = Application(qt, root / "snaploom", testing=True)
        original.workspace.set("save_folder", str(root / "exports"))
        original.workspace.set("save_pattern", "QA synthetic image")
        image = QImage(96, 80, QImage.Format.Format_RGB32)
        image.fill(QColor(*COLOR))
        try:
            original.output(image, "save-copy", silent=True, record=False)
            previous = probe("Snaploom v1.0.22 Save and Copy", root / "exports" / "QA synthetic image.png")
            assert previous["available"]["text"] and previous["available"]["file_drop"]

            original.copy_image(image)
            proposed = probe("Snaploom image-only publisher used by the candidate")
            assert proposed["consumer"] == "PNG" and proposed["available"] == {
                "image": True, "text": False, "file_drop": False,
            }

            config = f"--config={root / 'snipaste.ini'}"
            snipaste = subprocess.Popen([executable, config], cwd=root)
            try:
                surface = QWidget(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
                surface.setStyleSheet("background: #245886;")
                surface.showFullScreen()
                surface.raise_()
                QTest.qWait(2500)
                screen = qt.primaryScreen().grabWindow(0).toImage()
                sample = screen.pixelColor(screen.width() // 2, screen.height() // 2).getRgb()
                assert sample == COLOR, f"Hosted screen does not show the synthetic fixture: {sample}"

                sequence = user.GetClipboardSequenceNumber()
                for _ in range(3):
                    subprocess.run([executable, config, "snip", "--full", "-o", "clipboard"], cwd=root, timeout=5)
                    deadline = monotonic() + 5
                    while monotonic() < deadline and user.GetClipboardSequenceNumber() == sequence:
                        QTest.qWait(50)
                    if user.GetClipboardSequenceNumber() != sequence:
                        break
                else:
                    raise RuntimeError(f"Snipaste CLI did not write the clipboard (resident exit {snipaste.poll()})")
                snipaste_result = probe("Snipaste v2.11.3 native CLI")
                assert snipaste_result["available"]["image"] and not snipaste_result["available"]["text"]
                assert not snipaste_result["available"]["file_drop"]
            finally:
                snipaste.terminate()
                snipaste.wait(5)
        finally:
            if qt.clipboard().ownsClipboard():
                qt.clipboard().clear()
            original.shutdown()


if __name__ == "__main__":
    if sys.argv[1] == "read":
        inspect_clipboard(sys.argv[2])
    else:
        if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_OS") != "Windows":
            raise SystemExit("Run this probe only on the dedicated Windows GitHub Actions runner.")
        compare(Path(sys.argv[1]).resolve(strict=True))
