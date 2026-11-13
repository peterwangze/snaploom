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
import json
import sys
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageGrab

with patch("importlib.metadata.version", return_value="0.35.2"):
    from chrys.app.tui.widgets.chrome.image_paste import save_clipboard_image_to_file

with ImageGrab.grabclipboard() as clipboard:
    clipboard_format = clipboard.format
saved = save_clipboard_image_to_file(Path(sys.argv[1]))
assert saved and saved.is_file(), "iCode did not create an image attachment"
with Image.open(saved) as attachment:
    print(json.dumps({
        "format": clipboard_format,
        "size": attachment.size,
        "pixel": attachment.convert("RGBA").getpixel((2, 3)),
    }))
"""

qt = QApplication([])
with TemporaryDirectory(prefix="snaploom-icode-verify-") as temp:
    root = Path(temp)
    app = Application(qt, root / "profile", testing=True)
    try:
        for format_ in (QImage.Format.Format_RGB32, QImage.Format.Format_ARGB32_Premultiplied):
            fixture = QImage(16, 12, format_)
            fixture.fill(QColor("white"))
            fixture.setPixelColor(2, 3, QColor(32, 96, 168, 117))
            expected = list(fixture.pixelColor(2, 3).getRgb())
            for current in (False, True):
                if current:
                    app.copy_image(fixture)
                else:
                    mime = QMimeData()
                    mime.setImageData(fixture)
                    mime.setData("image/png", png_bytes(fixture))
                    qt.clipboard().setMimeData(mime)

                child = QProcess()
                environment = QProcessEnvironment.systemEnvironment()
                environment.insert("PYTHONPATH", os.environ["ICODE_SOURCE"])
                child.setProcessEnvironment(environment)
                try:
                    child.start(os.environ["ICODE_PYTHON"], ["-c", reader, str(root / "attachments")])
                    assert child.waitForStarted(5000), child.errorString()
                    deadline = monotonic() + 15
                    while child.state() != QProcess.ProcessState.NotRunning and monotonic() < deadline:
                        QTest.qWait(20)
                    output = bytes(child.readAllStandardOutput()).decode().strip()
                    errors = bytes(child.readAllStandardError()).decode(errors="replace")
                    print(format_.name, "PNG first" if current else "legacy", child.exitCode(), output, errors)
                    if current:
                        assert child.state() == QProcess.ProcessState.NotRunning
                        assert child.exitCode() == 0, errors
                        result = json.loads(output)
                        assert result == {"format": "PNG", "size": [16, 12], "pixel": expected}, result
                finally:
                    if child.state() != QProcess.ProcessState.NotRunning:
                        child.kill()
                        child.waitForFinished(2000)
    finally:
        if qt.clipboard().ownsClipboard():
            qt.clipboard().clear()
        app.shutdown()
