"""Verify a real packaged upgrade, relaunch and workspace restoration in an isolated CI account."""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import platform
import re
import subprocess
import sys
import tarfile
import tempfile
import time
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from PySide6.QtCore import QLockFile, QPoint, QRect
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

# Allow direct invocation: python tests/packaged_update_acceptance.py ...
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import snaploom
from snaploom.app import Application
from snaploom.capture import Snapshot
from snaploom.commands import send_to_instance
from snaploom.editor import Annotation
from snaploom.storage import Workspace, save_image
from snaploom.update_install import HELPER_NAME, WORK_DIR, process_running, read_handoff, start_update
from snaploom.update_restart_state import save_restart_state
from snaploom.updates import RELEASES_URL, Release, ReleaseAsset


SAVE_PATTERN = "Configured screenshot format from the previous version"
OPEN_IMAGE_COLOR = "#d41743"
UPDATE_TIMEOUT = 240


def run(args, env, *, timeout=150):
    result = subprocess.run(
        [str(arg) for arg in args], stdin=subprocess.DEVNULL, capture_output=True, env=env, timeout=timeout,
    )
    if result.returncode:
        output = (result.stdout + b"\n" + result.stderr).decode(errors="replace").strip()
        raise RuntimeError(f"{Path(args[0]).name} exited with {result.returncode}.\n{output[-3500:]}")
    return result.stdout


def install_baseline(package, directory, env):
    if sys.platform == "win32" and package.name.endswith(".zip"):
        install = directory / "Previous Windows Portable"
        with ZipFile(package) as archive:
            archive.extractall(install)
        executable = install / "Snaploom" / "Snaploom.exe"
        return executable, executable.with_name("Snaploom-cli.exe")
    if sys.platform == "linux" and package.name.endswith(".tar.gz"):
        install = directory / "Previous Linux Portable"
        with tarfile.open(package, "r:gz") as archive:
            archive.extractall(install, filter="data")
        executable = install / "Snaploom" / "Snaploom"
        return executable, executable
    if sys.platform == "win32":
        import winreg

        uninstall_key = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{653FFABA-0E9F-4804-AB44-41F2ED68A938}_is1"
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive, uninstall_key, 0, winreg.KEY_READ | view):
                        raise RuntimeError("The Windows install test needs an isolated account without a registered Snaploom installation.")
                except FileNotFoundError:
                    pass
        install = directory / "Previous Windows App"
        run([
            package, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOICONS", "/CURRENTUSER", f"/DIR={install}",
        ], env)
        return install / "Snaploom.exe", install / "Snaploom-cli.exe"
    if sys.platform == "darwin":
        from snaploom.update_install_macos import _mount_disk_image

        bundle = directory / "Previous Mac App.app"
        with _mount_disk_image(package, env) as mount:
            run(["/usr/bin/ditto", mount / "Snaploom.app", bundle], env)
        executable = bundle / "Contents" / "MacOS" / "Snaploom"
        return executable, executable
    run(["/bin/sh", package], env)
    executable = Path(env["XDG_DATA_HOME"]) / "snaploom" / "app" / "Snaploom"
    return executable, executable


def fixtures(qt, profile, directory):
    app = Application(qt, profile, testing=True)
    try:
        app.dashboard.show()
        app.workspace.set("save_pattern", SAVE_PATTERN)
        app.workspace.set("detect_windows", False)
        image = QImage(150, 90, QImage.Format.Format_RGB32)
        image.fill(QColor(OPEN_IMAGE_COLOR))
        original = save_image(image, directory / "Image to reopen.png")
        app.open_files([str(original)], QPoint(20, 20))
        pin_id, = app.pin_windows
        pin_editor = app.open_pin_editor(pin_id)
        pin_editor.canvas.document.commit((Annotation("text", ((25, 25),), text="Unapplied file edit"),))

        desktop = QImage(500, 350, QImage.Format.Format_RGB32)
        desktop.fill(QColor("#1e324c"))
        app.capturing = True
        app.capture_context = {"dashboard": True, "options": {"whiteboard": True}, "pins": []}
        app.open_capture_editor(Snapshot(desktop, desktop.rect()), QRect(10, 12, 180, 150),
                                app.capture_context["options"], None)
        app.editor.document.commit((Annotation("rectangle", ((10, 10), (70, 60)), color="#fdec44"),))
        qt.processEvents()
        assert pin_editor.isVisible(), "The original image has no open editable window"
        assert app.editor.isVisible(), "The screenshot editor did not open"
        return save_restart_state(app), pin_id
    finally:
        if app.editor is not None:
            app.editor.close()
        for widget in tuple(app.dialogs):
            widget.reject()
        for pin in app.pin_windows.values():
            pin.hide()
            pin.deleteLater()
        app.dashboard.hide()
        app.dashboard.deleteLater()
        app.shutdown()
        qt.processEvents()


def wait_until(condition, seconds, message):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = condition()
        if result:
            return result
        time.sleep(0.2)
    raise RuntimeError(message)


def lock_pid(lock):
    pid, _, _ = lock.getLockInfo()
    return pid if pid > 0 and process_running(pid) else None


def wait_for_old_app(lock, process):
    pid = lock_pid(lock)
    if process.poll() is not None:
        raise RuntimeError(f"The baseline app exited before login with code {process.returncode}.")
    if pid == process.pid and send_to_instance({"command": "hide", "block": 2}) == 0:
        return pid
    return None


def release_asset(package, version):
    with package.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    url = f"{RELEASES_URL}/download/v{version}/{package.name}"
    asset = ReleaseAsset(package.name, url, package.stat().st_size, digest)
    return Release(version, "", f"{RELEASES_URL}/tag/v{version}", (asset,), None), asset, digest


def wait_for_restarted_app(profile, lock, previous_pid, snapshot):
    handoff = read_handoff(profile)
    if handoff.get("status") == "failed" or (profile / "update-install.log").is_file():
        raise RuntimeError("The update worker reported an installation failure.")
    pid = lock_pid(lock)
    if pid and pid != previous_pid and not snapshot.exists():
        if send_to_instance({"command": "disable-hotkeys", "block": 2}) == 0:
            return pid
    return None


def assert_restored_capture():
    errors = io.StringIO()
    with redirect_stderr(errors):
        code = send_to_instance({"command": "snip", "block": 3})
    message = errors.getvalue()
    assert code == 1 and ("Finish the current capture first" in message or "请先完成当前截图" in message), (
        f"The updated process did not react as a restored screenshot editor: code {code}, {message!r}"
    )


def assert_restored_profile(profile, pin_id):
    workspace = Workspace(profile)
    try:
        assert workspace.get("save_pattern") == SAVE_PATTERN, "The saved preference changed during the update"
        (current_id, image, state), = workspace.pins()
        assert current_id == pin_id and state.get("visible", True), "The opened image pin was not preserved"
        assert image.pixelColor(75, 45) == QColor(OPEN_IMAGE_COLOR), "The restored image content has changed"
    finally:
        workspace.close()


def report_logs(profile, baseline_log):
    logs = [baseline_log, profile / "update-install.log"]
    if (profile / WORK_DIR).is_dir():
        logs.extend((profile / WORK_DIR).glob("worker-*/log.txt"))
    for log in logs:
        if log.is_file():
            contents = log.read_text(encoding="utf-8", errors="replace").strip()
            if contents:
                print(f"{log.name}:\n{contents[-5000:]}", file=sys.stderr)


def acceptance(old_package, new_package, helper):
    version = snaploom.VERSION
    match = re.match(r"^Snaploom-(\d+\.\d+\.\d+)-", old_package.name)
    if match is None:
        raise ValueError("The baseline must use an official Snaploom package name.")
    baseline_version = match[1]
    if helper.name != HELPER_NAME or not helper.is_file():
        raise RuntimeError(f"Expected the packaged {HELPER_NAME} executable, got {helper}")
    with tempfile.TemporaryDirectory(prefix="snaploom packaged upgrade ", ignore_cleanup_errors=True) as folder:
        directory = Path(folder).resolve()
        profile = directory / "Isolated Workspace"
        home = directory / "Test Home"
        home.mkdir()
        variables = {
            "HOME": str(home), "XDG_DATA_HOME": str(home / "share"), "SNAPLOOM_DATA_DIR": str(profile),
            "QT_QPA_PLATFORM": os.environ.get("QT_QPA_PLATFORM") or "offscreen",
        }
        env = os.environ | variables
        with patch.dict(os.environ, variables):
            qt = QApplication([])
            qt.setQuitOnLastWindowClosed(False)
            qt.setApplicationName("Snaploom")
            baseline = None
            executable = None
            cli = None
            lock = QLockFile(str(profile / "instance.lock"))
            baseline_log = directory / "baseline.log"
            try:
                snapshot, pin_id = fixtures(qt, profile, directory)
                executable, cli = install_baseline(old_package, directory, env)
                portable = old_package.name.endswith((".zip", ".tar.gz"))
                portable_note = executable.parent / "User file kept with their portable app.txt"
                if portable:
                    portable_note.write_text("Portable user file", encoding="utf-8")
                assert run([cli, "--version"], env, timeout=30).strip() == baseline_version.encode("ascii"), (
                    "The installed baseline does not match the required previous release."
                )
                with baseline_log.open("wb") as log:
                    baseline = subprocess.Popen([str(executable), "--background"], env=env,
                                                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
                old_pid = wait_until(lambda: wait_for_old_app(lock, baseline), 25, "The baseline did not start.")
                release, asset, digest = release_asset(new_package, version)
                with (
                    patch.object(snaploom, "VERSION", baseline_version),
                    patch.object(sys, "executable", str(executable)),
                    patch.object(sys, "frozen", True, create=True),
                    patch.object(sys, "_MEIPASS", str(helper.parent), create=True),
                    patch("snaploom.update_install.os.getpid", return_value=old_pid),
                ):
                    start_update(profile, release, asset, new_package, digest)
                assert read_handoff(profile).get("status") == "installing"
                assert lock_pid(lock) == old_pid, "The helper replaced the application while it was running"
                run([cli, "quit"], env, timeout=20)
                assert baseline.wait(20) == 0, "The previous release did not quit cleanly"
                new_pid = wait_until(
                    lambda: wait_for_restarted_app(profile, lock, old_pid, snapshot),
                    UPDATE_TIMEOUT, "The installed update did not restart and consume the draft.",
                )
                assert new_pid != old_pid
                assert run([cli, "--version"], env, timeout=30).strip() == version.encode("ascii"), (
                    "The restarted application does not match the new release."
                )
                assert_restored_capture()
                assert_restored_profile(profile, pin_id)
                if portable:
                    assert portable_note.read_text(encoding="utf-8") == "Portable user file", (
                        "Updating the portable app changed an unrelated user file."
                    )
                run([cli, "quit"], env, timeout=20)
                wait_until(lambda: not process_running(new_pid), 20, "The updated application did not quit.")
                print(f"Packaged {platform.system()} {platform.machine()} upgrade {baseline_version} -> {version}: "
                      "new process, editable draft, open image and settings verified.", flush=True)
            except Exception:
                report_logs(profile, baseline_log)
                raise
            finally:
                if lock_pid(lock):
                    send_to_instance({"command": "quit", "block": 3})
                if baseline is not None and baseline.poll() is None:
                    try:
                        baseline.wait(8)
                    except subprocess.TimeoutExpired:
                        baseline.kill()
                        baseline.wait(8)
                if sys.platform == "win32" and executable is not None and not portable:
                    uninstaller = executable.with_name("unins000.exe")
                    if uninstaller.is_file():
                        run([uninstaller, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], env, timeout=80)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-package", type=Path, required=True)
    parser.add_argument("--new-package", type=Path, required=True)
    parser.add_argument("--helper", type=Path, required=True)
    inputs = parser.parse_args()
    acceptance(*(path.resolve(strict=True) for path in (inputs.old_package, inputs.new_package, inputs.helper)))
