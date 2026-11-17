import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QObject, QStandardPaths, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtNetwork import QNetworkReply, QNetworkRequest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from snaploom.app import Application
from snaploom.update_dialog import UpdateDialog
from snaploom.update_transfer import UpdateTransfer
from snaploom.updates import LATEST_RELEASE_URL, UpdateMetadataError, expected_sha256, parse_release


VERSION = "99.10.2"
RELEASE_URL = f"https://github.com/peterwangze/snaploom/releases/tag/v{VERSION}"
ASSET_ROOT = f"https://github.com/peterwangze/snaploom/releases/download/v{VERSION}/"
NOTES = "## 更新内容\n\n- Smoother input.\n- Clearer controls.\n\n## 安装与兼容性\n\nInstaller table"
INSTALLER = f"Snaploom-{VERSION}-windows-x64-setup.exe"
PORTABLE = f"Snaploom-{VERSION}-windows-x64-portable.zip"
FIXTURE = b"verified installer fixture"
CHECKSUM = hashlib.sha256(FIXTURE).hexdigest()
MANIFEST = f"{CHECKSUM}  {INSTALLER}\n".encode("ascii")


def release_asset(name, content=FIXTURE):
    return {
        "name": name, "state": "uploaded", "size": len(content),
        "browser_download_url": ASSET_ROOT + name,
        "digest": "sha256:" + hashlib.sha256(content).hexdigest(),
    }


def release_payload(*assets):
    return {
        "tag_name": f"v{VERSION}", "html_url": RELEASE_URL, "body": NOTES,
        "draft": False, "prerelease": False, "assets": list(assets),
    }


class UpdateMetadataTests(unittest.TestCase):
    def test_release_compares_numeric_versions_and_selects_matching_packages(self):
        suffixes = [
            "windows-x64-setup.exe", "windows-x64-portable.zip", "macos-arm64.dmg", "macos-x64.dmg",
            "linux-arm64-setup.run", "linux-arm64.tar.gz", "linux-x64-setup.run", "linux-x64.tar.gz",
        ]
        release = parse_release(release_payload(*(release_asset(f"Snaploom-{VERSION}-{suffix}") for suffix in suffixes)))
        self.assertTrue(release.newer_than("99.9.9"))
        self.assertFalse(release.newer_than(VERSION))
        self.assertFalse(release.newer_than("100.0.0"))
        for system, machine, indexes in (
            ("Windows", "AMD64", (0, 1)), ("Darwin", "arm64", (2,)), ("Darwin", "x86_64", (3,)),
            ("Linux", "aarch64", (4, 5)), ("Linux", "x86_64", (6, 7)), ("Linux", "riscv64", ()),
        ):
            with self.subTest(system=system, machine=machine):
                self.assertEqual([asset.name for asset in release.packages(system, machine)],
                                 [f"Snaploom-{VERSION}-{suffixes[index]}" for index in indexes])

    def test_metadata_cannot_offer_lookalike_download_urls_or_prereleases(self):
        bad = release_asset(INSTALLER) | {"browser_download_url": f"https://github.com.example.org/{INSTALLER}"}
        with self.assertRaises(UpdateMetadataError):
            parse_release(release_payload(bad))
        for values in ({"draft": True}, {"prerelease": True}, {"tag_name": f"v{VERSION}-rc.1"},
                       {"html_url": "https://example.org/releases"}):
            with self.subTest(values=values), self.assertRaises(UpdateMetadataError):
                parse_release(release_payload() | values)

    def test_checksum_verifies_exact_name_and_agrees_with_the_release_digest(self):
        asset, = parse_release(release_payload(release_asset(INSTALLER))).packages("Windows", "AMD64")
        self.assertEqual(expected_sha256(MANIFEST, asset), CHECKSUM)
        for bad in (MANIFEST.replace(b"setup.exe", b"portable.zip"), b"0" * 64 + MANIFEST[64:]):
            with self.subTest(manifest=bad), self.assertRaises(UpdateMetadataError):
                expected_sha256(bad, asset)


class ScriptedReply(QObject):
    readyRead = Signal()
    finished = Signal()

    def __init__(self, request, parent, body, status, redirect, held):
        super().__init__(parent)
        self.body = body
        self.request = request
        self.attributes = {QNetworkRequest.Attribute.HttpStatusCodeAttribute: status}
        self.attributes[QNetworkRequest.Attribute.RedirectionTargetAttribute] = QUrl(redirect) if redirect else None
        self.done = False
        self.network_error = QNetworkReply.NetworkError.NoError
        if not held:
            QTimer.singleShot(0, self.deliver)

    def attribute(self, name):
        return self.attributes.get(name)

    def url(self):
        return self.request.url()

    def error(self):
        return self.network_error

    def isFinished(self):
        return self.done

    def setReadBufferSize(self, size):
        pass

    def bytesAvailable(self):
        return len(self.body)

    def read(self, maximum):
        data, self.body = self.body[:maximum], self.body[maximum:]
        return data

    def readAll(self):
        return self.read(len(self.body))

    def deliver(self):
        if self.isFinished():
            return
        self.readyRead.emit()
        if not self.isFinished():
            self.done = True
            self.finished.emit()

    def abort(self):
        if not self.isFinished():
            self.network_error = QNetworkReply.NetworkError.OperationCanceledError
            self.done = True
            self.finished.emit()


class ScriptedManager(QObject):
    def __init__(self, routes):
        super().__init__()
        self.routes = routes
        self.urls = []

    def get(self, request):
        url = request.url().toString()
        self.urls.append(url)
        body, status, redirect, held = self.routes.get(url, (b"", 404, None, False))
        return ScriptedReply(request, self, body, status, redirect, held)


def response(body, *, status=200, redirect=None, held=False):
    return body, status, redirect, held


class UpdateDownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setQuitOnLastWindowClosed(False)

    @staticmethod
    def pump_until(condition):
        for _ in range(200):
            if condition():
                return
            QTest.qWait(5)
        raise AssertionError("Update operation did not settle")

    @staticmethod
    def routes(*, download=FIXTURE, manifest=MANIFEST):
        payload = release_payload(release_asset(INSTALLER), release_asset(PORTABLE),
                                  release_asset("SHA256SUMS.txt", manifest))
        return {
            LATEST_RELEASE_URL: response(json.dumps(payload).encode("utf-8")),
            ASSET_ROOT + "SHA256SUMS.txt": response(manifest),
            ASSET_ROOT + INSTALLER: response(download),
        }

    @staticmethod
    def watch(client):
        events = []
        client.checked.connect(lambda release: events.append(("checked", release)))
        client.downloaded.connect(lambda path: events.append(("downloaded", path)))
        client.failed.connect(lambda code: events.append(("failed", code)))
        client.cancelled.connect(lambda: events.append(("cancelled", None)))
        return events

    def test_download_follows_github_cdn_and_publishes_only_verified_file(self):
        cdn = "https://release-assets.githubusercontent.com/signed-update"
        routes = self.routes()
        routes[ASSET_ROOT + INSTALLER] = response(b"", status=302, redirect=cdn)
        routes[cdn] = response(FIXTURE)
        manager = ScriptedManager(routes)
        client = UpdateTransfer(manager=manager)
        events = self.watch(client)
        client.check()
        self.pump_until(lambda: bool(events))
        name, release = events.pop()
        self.assertEqual(name, "checked")
        asset = release.packages("Windows", "AMD64")[0]
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / INSTALLER
            client.download(release, asset, target)
            self.assertFalse(target.exists())
            self.pump_until(lambda: bool(events))
            self.assertEqual(events, [("downloaded", target)])
            self.assertEqual(target.read_bytes(), FIXTURE)
            self.assertEqual(list(Path(folder).iterdir()), [target])
        self.assertEqual(manager.urls, [LATEST_RELEASE_URL, ASSET_ROOT + "SHA256SUMS.txt", asset.url, cdn])

    def test_bad_hash_unsafe_redirect_and_cancel_leave_no_installers_or_partial_files(self):
        for action in ("bad hash", "unsafe redirect", "cancel", "destination race"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as folder:
                routes = self.routes()
                if action == "bad hash":
                    routes[ASSET_ROOT + INSTALLER] = response(b"x" * len(FIXTURE))
                elif action == "unsafe redirect":
                    routes[ASSET_ROOT + INSTALLER] = response(b"", status=302, redirect="https://example.org/installer")
                elif action in ("cancel", "destination race"):
                    routes[ASSET_ROOT + INSTALLER] = response(FIXTURE, held=True)
                manager = ScriptedManager(routes)
                client = UpdateTransfer(manager=manager)
                events = self.watch(client)
                client.check()
                self.pump_until(lambda: bool(events))
                release = events.pop()[1]
                target = Path(folder) / INSTALLER
                client.download(release, release.packages("Windows", "AMD64")[0], target)
                if action in ("cancel", "destination race"):
                    self.pump_until(lambda: ASSET_ROOT + INSTALLER in manager.urls)
                    if action == "cancel":
                        client.cancel()
                    else:
                        target.write_bytes(b"User's file")
                        reply, = [reply for reply in manager.findChildren(ScriptedReply) if not reply.isFinished()]
                        reply.deliver()
                self.pump_until(lambda: bool(events))
                self.assertEqual(events[0][0], "cancelled" if action == "cancel" else "failed")
                self.assertFalse(client.busy)
                self.assertNotIn("https://example.org/installer", manager.urls)
                if action == "destination race":
                    self.assertEqual(list(Path(folder).iterdir()), [target])
                    self.assertEqual(target.read_bytes(), b"User's file")
                else:
                    self.assertEqual(list(Path(folder).iterdir()), [])

    def test_dialog_checks_latest_and_offers_release_page_when_offline(self):
        manager = ScriptedManager(self.routes())
        client = UpdateTransfer(manager=manager)
        dialog = UpdateDialog(lambda en, zh: zh, client=client, current_version=VERSION)
        self.addCleanup(dialog.close)
        dialog.show()
        self.assertFalse(manager.urls)
        dialog.check_for_updates()
        self.pump_until(lambda: not client.busy)
        self.assertIn("最新版", dialog.status.text())
        self.assertIn(f"v{VERSION}", dialog.versions.text())
        self.assertTrue(dialog.download.isHidden())
        manager.routes[LATEST_RELEASE_URL] = response(b"", status=503)
        QTest.mouseClick(dialog.recheck, Qt.MouseButton.LeftButton)
        self.pump_until(lambda: not client.busy)
        self.assertIn("无法检查更新", dialog.status.text())
        with patch.object(QDesktopServices, "openUrl", return_value=True) as open_url:
            QTest.mouseClick(dialog.release_page, Qt.MouseButton.LeftButton)
        self.assertEqual(open_url.call_args.args[0].toString(), "https://github.com/peterwangze/snaploom/releases")

    def test_dialog_offers_matching_packages_and_downloads_without_overwriting(self):
        manager = ScriptedManager(self.routes())
        client = UpdateTransfer(manager=manager)
        dialog = UpdateDialog(lambda en, zh: en, client=client, current_version="1.0.20", install_kind="installer")
        self.addCleanup(dialog.close)
        dialog.show()
        with patch("snaploom.updates.platform.system", return_value="Windows"), patch(
            "snaploom.updates.platform.machine", return_value="AMD64",
        ):
            dialog.check_for_updates()
            self.pump_until(lambda: not client.busy)
        self.assertIn(f"v{VERSION}", dialog.status.text())
        self.assertIn("Smoother input.", dialog.notes.toPlainText())
        self.assertNotIn("Installer table", dialog.notes.toPlainText())
        self.assertEqual(dialog.package.count(), 2)
        self.assertTrue(dialog.install.isHidden())
        self.assertIn("Installer", dialog.package.itemText(0))
        self.assertIn("Portable", dialog.package.itemText(1))
        self.assertEqual(manager.urls, [LATEST_RELEASE_URL])
        with tempfile.TemporaryDirectory() as folder:
            original = Path(folder) / INSTALLER
            original.write_bytes(b"existing installer")
            with patch.object(QStandardPaths, "writableLocation", return_value=folder):
                QTest.mouseClick(dialog.download, Qt.MouseButton.LeftButton)
                self.pump_until(lambda: not client.busy)
            destinations = set(Path(folder).iterdir()) - {original}
            target, = destinations
            self.assertEqual(original.read_bytes(), b"existing installer")
            self.assertEqual(target.read_bytes(), FIXTURE)
            self.assertIn("Verified", dialog.download_status.text())
            self.assertIn(target.name, dialog.download_status.text())
            self.assertTrue(dialog.install.isVisible())
            install_calls = []
            dialog.install_requested.connect(lambda *args: install_calls.append(args))
            QTest.mouseClick(dialog.install, Qt.MouseButton.LeftButton)
            release, asset, chosen_path, digest = install_calls[0]
            self.assertEqual((release.version, asset.name, chosen_path, digest), (VERSION, INSTALLER, target, CHECKSUM))
            self.assertFalse(dialog.install.isEnabled())
            dialog.install_failed("Couldn’t start the installer.")
            self.assertTrue(dialog.install.isEnabled())
            with patch.object(QDesktopServices, "openUrl", return_value=True) as open_url:
                QTest.mouseClick(dialog.open_folder, Qt.MouseButton.LeftButton)
            self.assertEqual(Path(open_url.call_args.args[0].toLocalFile()), Path(folder))

    def test_newer_release_without_matching_package_or_checksum_offers_browser_fallback(self):
        for machine, assets, message in (
            ("ARM64", (release_asset(INSTALLER), release_asset("SHA256SUMS.txt", MANIFEST)), "没有适合本机"),
            ("AMD64", (release_asset(INSTALLER),), "校验"),
        ):
            with self.subTest(machine=machine):
                routes = {LATEST_RELEASE_URL: response(json.dumps(release_payload(*assets)).encode("utf-8"))}
                client = UpdateTransfer(manager=ScriptedManager(routes))
                dialog = UpdateDialog(lambda en, zh: zh, client=client, current_version="1.0.20")
                self.addCleanup(dialog.close)
                dialog.show()
                with patch("snaploom.updates.platform.system", return_value="Windows"), patch(
                    "snaploom.updates.platform.machine", return_value=machine,
                ):
                    dialog.check_for_updates()
                    self.pump_until(lambda: not client.busy)
                self.assertIn(message, dialog.download_status.text())
                self.assertTrue(dialog.download.isHidden() or not dialog.download.isEnabled())
                with patch.object(QDesktopServices, "openUrl", return_value=True) as open_url:
                    QTest.mouseClick(dialog.release_page, Qt.MouseButton.LeftButton)
                self.assertEqual(open_url.call_args.args[0].toString(), RELEASE_URL)

    def test_workspace_and_tray_share_one_on_demand_update_check(self):
        manager = ScriptedManager({LATEST_RELEASE_URL: response(b"", held=True)})
        client = UpdateTransfer(manager=manager)
        with tempfile.TemporaryDirectory() as folder:
            app = Application(self.qt, Path(folder), testing=True)
            try:
                app.show_dashboard()
                button = app.dashboard.findChild(QPushButton, "UpdateCheck")
                tray = next(action for action in app.tray_menu.actions()
                            if action.text() == app.tr("Check for updates…", "检查更新…"))
                self.assertEqual(manager.urls, [])
                with patch("snaploom.update_dialog.UpdateDialog", side_effect=lambda translate, **options: UpdateDialog(
                    translate, client=client, **options,
                )):
                    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                    dialog = app.update_dialog
                    self.assertIsNotNone(dialog)
                    self.assertTrue(dialog.isVisible())
                    tray.trigger()
                    self.assertIs(app.update_dialog, dialog)
                    self.assertEqual(manager.urls, [LATEST_RELEASE_URL])
                    dialog.close()
                    self.assertIsNone(app.update_dialog)
                    self.assertFalse(client.busy)
            finally:
                app.shutdown()
                app.dashboard.close()
                app.dashboard.deleteLater()

    def test_github_star_opens_only_the_project_when_clicked(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(
            QDesktopServices, "openUrl", return_value=True,
        ) as open_url:
            app = Application(self.qt, Path(folder), testing=True)
            try:
                app.show_dashboard()
                open_url.assert_not_called()
                button = app.dashboard.findChild(QPushButton, "GitHubStar")
                self.assertTrue(button.isVisible() and button.isFlat())
                QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                open_url.assert_called_once()
                self.assertEqual(open_url.call_args.args[0].toString(), "https://github.com/peterwangze/snaploom")
            finally:
                app.shutdown()
                app.dashboard.close()
                app.dashboard.deleteLater()
