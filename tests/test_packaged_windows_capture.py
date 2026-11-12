"""Exercise live capture and installer behavior with the exact Windows packages."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import monotonic
import unittest
from unittest.mock import patch

from PySide6.QtCore import QLockFile, QPoint, QProcess, QProcessEnvironment, QRect, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from snaploom.commands import send_to_instance


PACKAGED_CLI = os.environ.get("SNAPLOOM_PACKAGED_WINDOWS_CLI")
PACKAGED_SETUP = os.environ.get("SNAPLOOM_PACKAGED_WINDOWS_SETUP")


def packaged_process(profile):
    process = QProcess()
    environment = QProcessEnvironment.systemEnvironment()
    environment.insert("SNAPLOOM_DATA_DIR", str(profile))
    environment.insert("QT_QPA_PLATFORM", "windows")
    process.setProcessEnvironment(environment)
    return process


class PackagedProcessTests(unittest.TestCase):
    def run_process(self, executable, arguments, profile, *, timeout):
        process = packaged_process(profile)
        try:
            process.start(str(executable), arguments)
            self.assertTrue(process.waitForStarted(10000), process.errorString())
            deadline = monotonic() + timeout
            while process.state() != QProcess.ProcessState.NotRunning and monotonic() < deadline:
                QTest.qWait(25)
            self.assertEqual(process.state(), QProcess.ProcessState.NotRunning, f"{executable.name} timed out")
            stderr = bytes(process.readAllStandardError()).decode(errors="replace")
            self.assertEqual(process.exitStatus(), QProcess.ExitStatus.NormalExit, stderr)
            return process.exitCode(), stderr
        finally:
            if process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
                process.waitForFinished(5000)


@unittest.skipUnless(sys.platform == "win32" and PACKAGED_CLI, "requires the packaged Windows CLI")
class PackagedWindowsCaptureTests(PackagedProcessTests):
    def test_live_region_writes_native_pixels_from_the_desktop(self):
        executable = Path(PACKAGED_CLI).resolve(strict=True)
        self.assertTrue(executable.is_file())
        qt = QApplication.instance() or QApplication([])
        self.assertEqual(qt.platformName(), "windows")

        screen = qt.primaryScreen()
        available = screen.availableGeometry()
        bounds = QRect(QPoint(), QSize(320, 220).boundedTo(available.size()))
        bounds.moveCenter(available.center())
        fixture = QWidget(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.addCleanup(fixture.close)
        color = QColor("#e32191")
        palette = fixture.palette()
        palette.setColor(QPalette.ColorRole.Window, color)
        fixture.setPalette(palette)
        fixture.setAutoFillBackground(True)
        fixture.setGeometry(bounds)
        fixture.show()
        fixture.raise_()
        fixture.activateWindow()
        self.assertTrue(QTest.qWaitForWindowExposed(fixture, 3000))
        self.assert_fixture_visible_at_center(fixture)

        size = QSize(80, 64)
        origin = screen.geometry().topLeft()
        center = fixture.geometry().center() - origin
        # Align the region to whole device pixels at common quarter-step Windows scale factors.
        start = QPoint((center.x() - size.width() // 2) // 8 * 8,
                       (center.y() - size.height() // 2) // 8 * 8) + origin
        region = QRect(start, size)
        self.assertTrue(fixture.geometry().contains(region))

        with TemporaryDirectory(prefix="snaploom-packaged-capture-") as temporary:
            directory = Path(temporary)
            target = directory / "desktop fixture.png"
            arguments = ["snip", "--area", *(str(value) for value in region.getRect()), "--block", "20", "-o", str(target)]
            code, stderr = self.run_process(executable, arguments, directory / "profile", timeout=30)
            self.assertEqual(code, 0, stderr)
            self.assertTrue(target.is_file(), "Packaged capture did not save a screenshot")
            image = QImage(str(target))
            scale = fixture.devicePixelRatioF()
            self.assertEqual(image.size(), QSize(round(size.width() * scale), round(size.height() * scale)))
            for point in (image.rect().topLeft(), image.rect().topRight(), image.rect().center(),
                          image.rect().bottomLeft(), image.rect().bottomRight()):
                self.assertEqual(image.pixelColor(point), color, f"Unexpected captured desktop pixel at {point}")

    def assert_fixture_visible_at_center(self, fixture):
        # Qt exposure alone does not guarantee the fixture is on the interactive desktop.
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
        user.GetWindowRect.restype = wintypes.BOOL
        user.WindowFromPoint.argtypes = (wintypes.POINT,)
        user.WindowFromPoint.restype = wintypes.HWND
        handle = int(fixture.winId())
        bounds = wintypes.RECT()
        self.assertTrue(user.GetWindowRect(handle, ctypes.byref(bounds)))
        center = wintypes.POINT((bounds.left + bounds.right) // 2, (bounds.top + bounds.bottom) // 2)
        deadline = monotonic() + 3
        while user.WindowFromPoint(center) != handle and monotonic() < deadline:
            QTest.qWait(25)
        self.assertEqual(user.WindowFromPoint(center), handle, "Windows desktop is not exposing the test fixture")


@unittest.skipUnless(sys.platform == "win32" and PACKAGED_CLI and PACKAGED_SETUP, "requires the Windows installer")
class PackagedWindowsInstallerTests(PackagedProcessTests):
    def test_setup_refuses_to_replace_files_while_the_installed_app_is_running(self):
        cli = Path(PACKAGED_CLI).resolve(strict=True)
        setup = Path(PACKAGED_SETUP).resolve(strict=True)
        executable = cli.with_name("Snaploom.exe")
        self.assertTrue(executable.is_file())
        qt = QApplication.instance() or QApplication([])
        self.assertEqual(qt.platformName(), "windows")

        with TemporaryDirectory(prefix="snaploom-packaged-upgrade-") as temporary:
            directory = Path(temporary)
            profile = directory / "profile"
            lock_path = profile / "instance.lock"
            lock = QLockFile(str(lock_path))
            log_path = directory / "setup.log"
            app = packaged_process(profile)
            with patch.dict(os.environ, {"SNAPLOOM_DATA_DIR": str(profile)}):
                try:
                    app.start(str(executable), [])
                    self.assertTrue(app.waitForStarted(10000), app.errorString())
                    pid = app.processId()
                    self.wait_for_workspace(app, lock, pid)

                    arguments = [
                        "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/DIR={cli.parent}", f"/LOG={log_path}",
                    ]
                    code, stderr = self.run_process(setup, arguments, profile, timeout=45)
                    log = log_path.read_text(encoding="utf-8-sig", errors="replace") if log_path.is_file() else ""
                    self.assertEqual(code, 7, stderr or "\n".join(log.splitlines()[-30:]))
                    self.assertIn("Snaploom setup blocked by running instance", log)
                    self.assertEqual(app.state(), QProcess.ProcessState.Running)
                    self.assertEqual(lock.getLockInfo()[0], pid)
                    self.assertEqual(send_to_instance({"command": "show", "block": 3}), 0)

                    code, stderr = self.run_process(cli, ["quit"], profile, timeout=15)
                    self.assertEqual(code, 0, stderr)
                    if app.state() != QProcess.ProcessState.NotRunning:
                        self.assertTrue(app.waitForFinished(5000), "The installed app did not quit")
                    self.assertEqual(app.exitStatus(), QProcess.ExitStatus.NormalExit)
                    self.assertEqual(app.exitCode(), 0)
                    self.assertFalse(lock_path.exists())
                finally:
                    if app.state() != QProcess.ProcessState.NotRunning:
                        app.kill()
                        app.waitForFinished(5000)

    def wait_for_workspace(self, app, lock, pid):
        deadline = monotonic() + 15
        while app.state() == QProcess.ProcessState.Running and monotonic() < deadline:
            if lock.getLockInfo()[0] == pid and send_to_instance({"command": "show", "block": 3}) == 0:
                return
            QTest.qWait(100)
        stderr = bytes(app.readAllStandardError()).decode(errors="replace")
        self.fail(f"The installed Snaploom workspace did not respond. {stderr}")
