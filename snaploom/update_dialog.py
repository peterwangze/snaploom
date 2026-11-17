"""User-initiated release checks and package downloads."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from PySide6.QtCore import QStandardPaths, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout,
)

from . import VERSION
from .update_install import package_kind
from .update_transfer import UpdateTransfer
from .updates import RELEASES_URL


_ERRORS = {
    "network": ("Check your internet connection and try again.", "请检查网络连接后重试。"),
    "timeout": ("The connection timed out. Try again.", "连接超时，请重试。"),
    "response": ("GitHub could not complete the request. Try again later.", "GitHub 未能完成请求，请稍后重试。"),
    "release": ("The release information could not be read.", "无法读取发布信息。"),
    "manifest": ("The package checksum is unavailable or invalid.", "缺少有效的安装包校验信息。"),
    "checksum": ("The download did not pass the checksum check.", "下载的文件未通过完整性校验。"),
    "size": ("The download is incomplete or has the wrong size.", "文件未下载完整或大小不匹配。"),
    "write": ("Check that the Downloads folder is writable and has free space.", "请确认下载文件夹可写入，并且有足够的可用空间。"),
    "unsafe_url": ("The request left the official download site.", "请求离开了官方下载站点。"),
    "destination_exists": ("A file already exists at the download path. Try again.", "下载路径上已有同名文件，请重试。"),
}


def _notes_text(markdown: str) -> str:
    """Keep notes readable without rendering remote links, HTML, or images."""
    text = markdown[:24_000].replace("\r\n", "\n").strip()
    text = text.removeprefix("## 更新内容\n").split("\n## 安装与兼容性\n", 1)[0]
    text = re.sub(r"!?\[([^\[\]\r\n]*)\]\([^\r\n)]*\)", r"\1", text)
    text = re.sub(r"^[ \t]{0,3}#{1,6}[ \t]+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*([^*\r\n]+)\*\*", r"\1", text)
    return re.sub(r"`([^`\r\n]+)`", r"\1", text).strip()


def _download_target(name: str) -> Path:
    location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
    directory = Path(location) if location else Path.home() / "Downloads"
    directory.mkdir(parents=True, exist_ok=True)
    # Treat .tar.gz as one extension so duplicate archives stay easy to recognize.
    suffix = ".tar.gz" if name.endswith(".tar.gz") else Path(name).suffix
    stem = name.removesuffix(suffix)
    target = directory / name
    index = 2
    while target.exists() or target.is_symlink():
        target = directory / f"{stem} ({index}){suffix}"
        index += 1
    return target


class UpdateDialog(QDialog):
    install_requested = Signal(object, object, object, str)  # release, asset, path, digest

    def __init__(self, translate, parent=None, *, client=None, current_version=VERSION, install_kind=None):
        super().__init__(parent)
        self.tr = translate
        self.current_version = current_version
        self.install_kind = install_kind
        self.client = client if client is not None else UpdateTransfer(self)
        self._release = None
        self._task = None
        self._active_package = None
        self._saved_packages = {}

        self.setObjectName("UpdateDialog")
        self.setWindowTitle(self.tr("Check for updates", "检查更新"))
        self.resize(580, 180)
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        self.status = self._label("UpdateStatus")
        font = self.status.font()
        font.setBold(True)
        self.status.setFont(font)
        layout.addWidget(self.status)
        self.versions = self._label("UpdateVersions")
        layout.addWidget(self.versions)
        self._set_versions()

        self.notes_title = self._label("UpdateNotesTitle")
        self.notes_title.setText(self.tr("What’s new", "更新内容"))
        layout.addWidget(self.notes_title)
        self.notes = QPlainTextEdit(objectName="UpdateNotes")
        self.notes.setReadOnly(True)
        self.notes.setAccessibleName(self.notes_title.text())
        self.notes.setMinimumHeight(100)
        layout.addWidget(self.notes, 1)

        package_row = QHBoxLayout()
        self.package_label = self._label("UpdatePackageLabel")
        self.package_label.setText(self.tr("Package for this device", "适合本机的更新包"))
        package_row.addWidget(self.package_label)
        self.package = QComboBox(objectName="UpdatePackage")
        self.package.setAccessibleName(self.package_label.text())
        self.package.setMinimumContentsLength(18)
        self.package.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.package.currentIndexChanged.connect(self._package_changed)
        package_row.addWidget(self.package, 1)
        layout.addLayout(package_row)

        self.progress = QProgressBar(objectName="UpdateProgress")
        self.progress.setAccessibleName(self.tr("Download progress", "下载进度"))
        layout.addWidget(self.progress)
        self.download_status = self._label("UpdateDownloadStatus")
        self.download_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.download_status)
        layout.addStretch()

        actions = QHBoxLayout()
        self.release_page = self._button("UpdateReleasePage", "Release page", "查看发布页", self._open_release_page)
        self.open_folder = self._button("UpdateOpenFolder", "Open download folder", "打开下载位置", self._open_folder)
        actions.addWidget(self.release_page)
        actions.addWidget(self.open_folder)
        actions.addStretch()
        self.recheck = self._button("UpdateRecheck", "Check again", "重新检查", self.check_for_updates)
        self.download = self._button("UpdateDownload", "Download", "下载更新", self._start_download)
        self.install = self._button("UpdateInstall", "Install and restart", "安装并重启", self._request_install)
        self.cancel = self._button("UpdateCancel", "Cancel", "取消", self._cancel_task)
        self.close_button = self._button("UpdateClose", "Close", "关闭", self.reject)
        for button in (self.recheck, self.download, self.install, self.cancel, self.close_button):
            actions.addWidget(button)
        layout.addLayout(actions)

        self.client.checked.connect(self._checked)
        self.client.progress.connect(self._progressed)
        self.client.downloaded.connect(self._downloaded)
        self.client.failed.connect(self._failed)
        self.client.cancelled.connect(self._cancelled)
        self.finished.connect(self._cancel_task)
        self._sync_controls()

    @staticmethod
    def _label(name):
        label = QLabel(objectName=name)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        return label

    def _button(self, name, english, chinese, action):
        button = QPushButton(self.tr(english, chinese), objectName=name)
        button.setAutoDefault(False)
        button.clicked.connect(action)
        return button

    def _set_versions(self, latest=None):
        current = self.current_version
        self.versions.setText(self.tr(f"Current version: v{current}", f"当前版本 v{current}"))
        if latest is not None:
            self.versions.setText(self.versions.text() + self.tr(f"  ·  Latest: v{latest}", f"  ·  最新版 v{latest}"))

    def _set_feedback(self, text=""):
        self.download_status.setText(text)
        self.download_status.setVisible(bool(text))

    def _sync_controls(self):
        checking = self._task == "check"
        downloading = self._task == "download"
        installing = self._task == "install"
        busy = checking or downloading or installing
        has_packages = self.package.count() > 0
        can_verify = self._release is not None and self._release.checksum is not None
        current = self.package.currentData()
        saved = current is not None and current.name in self._saved_packages
        can_install = saved and package_kind(current.name) == self.install_kind
        has_notes = self._release is not None and self._release.newer_than(self.current_version)

        for widget in (self.notes_title, self.notes):
            widget.setVisible(has_notes)
        for widget in (self.package_label, self.package):
            widget.setVisible(has_packages)
        self.download.setVisible(has_packages and can_verify and not saved)
        self.package.setEnabled(not busy)
        self.recheck.setEnabled(not busy)
        self.download.setEnabled(not busy and not saved)
        self.install.setVisible(can_install)
        self.install.setEnabled(can_install and not busy)
        self.close_button.setEnabled(not installing)
        self.progress.setVisible(downloading)
        self.cancel.setVisible(checking or downloading)
        self.cancel.setText(self.tr("Cancel download", "取消下载") if downloading else self.tr("Cancel check", "取消检查"))
        self.open_folder.setVisible(saved and not busy)

    def check_for_updates(self):
        if self._task is not None:
            return
        self._task = "check"
        self._release = None
        self._saved_packages.clear()
        self.package.clear()
        self._set_feedback()
        self._set_versions()
        self.status.setText(self.tr("Checking for updates…", "正在检查更新…"))
        self._sync_controls()
        self.client.check()

    def _checked(self, release):
        if self._task != "check":
            return
        self._release = release
        self._task = None
        self._set_versions(release.version)
        if not release.newer_than(self.current_version):
            if release.version == self.current_version:
                self.status.setText(self.tr("You’re up to date.", "当前已是最新版。"))
            else:
                self.status.setText(self.tr("Your version is newer than the latest public release.", "当前版本比最新公开版本更新。"))
            self._sync_controls()
            return

        self.status.setText(self.tr(f"Snaploom v{release.version} is available.", f"发现新版 Snaploom v{release.version}。"))
        self.notes.setPlainText(_notes_text(release.notes) or self.tr("No release notes were provided.", "此版本暂无更新说明。"))
        if self.height() < 410:
            self.resize(self.width(), 410)
        packages = release.packages()
        if self.install_kind:
            packages = sorted(packages, key=lambda asset: package_kind(asset.name) != self.install_kind)
        for asset in packages:
            self.package.addItem(self._package_text(asset), asset)
        if not self.package.count():
            self._set_feedback(self.tr(
                "This release has no package for your device. See the release page for details.",
                "此版本没有适合本机的安装包，可在发布页查看详情。",
            ))
        elif release.checksum is None:
            self._set_feedback(self.tr(
                "No checksum is available for an in-app download. See the release page for downloads.",
                "此版本未提供校验文件，请前往发布页下载。",
            ))
        self._sync_controls()

    def _package_text(self, asset):
        name = asset.name
        _, _, system, machine, *_ = name.split("-")
        system = {"windows": "Windows", "macos": "macOS", "linux": "Linux"}[system]
        machine = machine.split(".", 1)[0]
        portable = name.endswith((".zip", ".tar.gz"))
        kind = self.tr("Portable", "免安装版") if portable else self.tr("Installer", "安装版")
        extension = "TAR.GZ" if name.endswith(".tar.gz") else Path(name).suffix[1:].upper()
        size = asset.size / (1024 * 1024)
        return f"{system} {machine} · {kind} ({extension}) · {size:.1f} MB"

    def _package_changed(self):
        if self._task is None and self._release is not None and self._release.checksum is not None:
            asset = self.package.currentData()
            saved = self._saved_packages.get(asset.name) if asset is not None else None
            if saved is None:
                self._set_feedback()
            else:
                self._show_saved_package(saved[0], asset)
        self._sync_controls()

    def _start_download(self):
        asset = self.package.currentData()
        if self._task is not None or asset is None or self._release is None or self._release.checksum is None:
            return
        try:
            target = _download_target(asset.name)
        except OSError:
            self._set_feedback(self.tr(*_ERRORS["write"]))
            return
        self._task = "download"
        self._active_package = asset
        self.progress.setRange(0, 0)
        self._set_feedback(self.tr(f"Downloading to {target}", f"正在下载到 {target}"))
        self._sync_controls()
        self.client.download(self._release, asset, target)

    def _progressed(self, received, total):
        if self._task == "download" and total > 0:
            self.progress.setRange(0, 100)
            self.progress.setValue(max(0, min(100, received * 100 // total)))

    def _downloaded(self, path, digest):
        if self._task != "download":
            return
        asset = self._active_package
        path = Path(path)
        self._task = None
        self._active_package = None
        self._saved_packages[asset.name] = (path, digest)
        self._show_saved_package(path, asset)
        self._sync_controls()

    def _show_saved_package(self, path, asset):
        if package_kind(asset.name) == self.install_kind:
            instruction = self.tr("Install and restart will restore your open work.",
                                  "安装并重启后会恢复当前打开的内容。")
        elif asset.name.endswith((".zip", ".tar.gz")):
            instruction = self.tr("Extract the package to use the new version.", "解压后即可使用新版。")
        elif asset.name.endswith(".run"):
            command = f"sh {shlex.quote(str(path))}"
            instruction = self.tr(f"Run this command to install the update:\n{command}", f"运行以下命令安装更新：\n{command}")
        else:
            instruction = self.tr("Open the downloaded package to install the update.", "打开下载的安装包即可安装更新。")
        self._set_feedback(self.tr(f"Verified and saved to {path}\n{instruction}", f"校验通过，已保存到 {path}\n{instruction}"))

    def _request_install(self):
        asset = self.package.currentData()
        if self._task is not None or asset is None or package_kind(asset.name) != self.install_kind:
            return
        saved = self._saved_packages.get(asset.name)
        if saved is None:
            return
        self._task = "install"
        self._set_feedback(self.tr("Saving your work before installing…", "正在保存当前工作，准备安装…"))
        self._sync_controls()
        self.install_requested.emit(self._release, asset, *saved)

    def install_failed(self, message):
        self._task = None
        self._set_feedback(message)
        self._sync_controls()

    def _failed(self, code):
        task, self._task = self._task, None
        if task is None:
            return
        self._active_package = None
        detail = self.tr(*_ERRORS.get(code, ("Please try again later.", "请稍后重试。")))
        if task == "check":
            self.status.setText(self.tr("Couldn’t check for updates.", "无法检查更新。"))
            self._set_feedback(detail)
        else:
            self._set_feedback(self.tr("Download failed. ", "下载失败。") + detail)
        self._sync_controls()

    def _cancel_task(self):
        if self._task in {"check", "download"}:
            self.client.cancel()

    def _cancelled(self):
        task, self._task = self._task, None
        self._active_package = None
        if task == "check":
            self.status.setText(self.tr("Update check cancelled.", "已取消检查更新。"))
        elif task == "download":
            self._set_feedback(self.tr("Download cancelled.", "已取消下载。"))
        self._sync_controls()

    def _open_release_page(self):
        url = self._release.url if self._release is not None else RELEASES_URL
        if not QDesktopServices.openUrl(QUrl(url)):
            self._set_feedback(self.tr(f"Could not open {url}", f"无法打开 {url}"))

    def _open_folder(self):
        current = self.package.currentData()
        saved = self._saved_packages.get(current.name) if current is not None else None
        path = saved[0] if saved is not None else None
        if path is not None and not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent))):
            self._set_feedback(self.tr(f"Could not open {path.parent}", f"无法打开 {path.parent}"))
