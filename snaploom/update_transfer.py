"""Asynchronous GitHub release checks and verified installer downloads."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QTemporaryFile, QUrl, Signal, Slot
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from . import VERSION
from .updates import (
    CHECKSUM_NAME, LATEST_RELEASE_URL, RELEASES_URL, Release, ReleaseAsset, UpdateMetadataError,
    expected_sha256, parse_release,
)


_ASSET_HOSTS = {"release-assets.githubusercontent.com", "objects.githubusercontent.com"}
_METADATA_LIMIT = 1024 * 1024
_MANIFEST_LIMIT = 64 * 1024
_BLOCK = 256 * 1024
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_ATTR = QNetworkRequest.Attribute


@dataclass
class _Download:
    asset: ReleaseAsset
    manifest: ReleaseAsset
    destination: Path
    expected: str = ""
    output: QTemporaryFile | None = None
    sha256: object = field(default_factory=hashlib.sha256)
    received: int = 0


def _canonical_asset(release: Release, asset: ReleaseAsset) -> bool:
    return (
        re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", release.version) is not None
        and re.fullmatch(r"[a-zA-Z0-9._-]+", asset.name) is not None
        and asset.name not in {".", ".."}
        and asset.url == f"{RELEASES_URL}/download/v{release.version}/{asset.name}"
    )


def _allowed_redirect(target: QUrl) -> bool:
    return (
        target.isValid()
        and target.scheme() == "https"
        and target.host() in _ASSET_HOSTS
        and target.port(443) == 443
        and not target.userInfo()
        and not target.hasFragment()
    )


class UpdateTransfer(QObject):
    """One update operation at a time. Terminal signals are emitted after cleanup."""

    checked = Signal(object)  # Release
    progress = Signal(object, object)  # Python int bytes received, total
    downloaded = Signal(object)  # Path
    failed = Signal(str)  # Stable error code for the translated UI.
    cancelled = Signal()

    def __init__(self, parent=None, *, manager=None):
        super().__init__(parent)
        self._manager = manager if manager is not None else QNetworkAccessManager(self)
        self._reply: QNetworkReply | None = None
        self._stage = ""
        self._download: _Download | None = None
        self._body = bytearray()
        self._redirects = 0

    @property
    def busy(self):
        return bool(self._stage)

    def check(self):
        if self.busy:
            return
        self._stage = "release"
        self._request(QUrl(LATEST_RELEASE_URL))

    def download(self, release: Release, asset: ReleaseAsset, destination: Path):
        if self.busy:
            return
        manifest = release.checksum
        if manifest is None or manifest.name != CHECKSUM_NAME or not 0 < manifest.size <= _MANIFEST_LIMIT:
            self.failed.emit("manifest")
            return
        if not _canonical_asset(release, asset) or not _canonical_asset(release, manifest):
            self.failed.emit("unsafe_url")
            return
        try:
            destination = Path(destination).absolute()
            if os.path.lexists(destination):
                self.failed.emit("destination_exists")
                return
            destination.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            self.failed.emit("write")
            return
        self._stage = "manifest"
        self._download = _Download(asset, manifest, destination)
        self._request(QUrl(manifest.url))

    def cancel(self):
        if self.busy:
            self._reset()
            self.cancelled.emit()

    def _request(self, url):
        request = QNetworkRequest(url)
        request.setAttribute(_ATTR.RedirectPolicyAttribute, QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        request.setAttribute(_ATTR.CacheLoadControlAttribute, QNetworkRequest.CacheLoadControl.AlwaysNetwork)
        request.setAttribute(_ATTR.CacheSaveControlAttribute, False)
        request.setAttribute(_ATTR.CookieLoadControlAttribute, QNetworkRequest.LoadControl.Manual)
        request.setAttribute(_ATTR.CookieSaveControlAttribute, QNetworkRequest.LoadControl.Manual)
        request.setAttribute(_ATTR.AuthenticationReuseAttribute, QNetworkRequest.LoadControl.Manual)
        request.setTransferTimeout(15_000 if self._stage != "package" else 30_000)
        request.setRawHeader(b"User-Agent", f"Snaploom/{VERSION}".encode("ascii"))
        request.setRawHeader(b"Accept-Encoding", b"identity")
        if self._stage == "release":
            request.setRawHeader(b"Accept", b"application/vnd.github+json")
        reply = self._manager.get(request)
        self._reply = reply
        reply.setReadBufferSize(_BLOCK)
        reply.readyRead.connect(self._ready_read)
        reply.finished.connect(self._reply_finished)
        if reply.isFinished():
            self._finished(reply)

    @Slot()
    def _ready_read(self):
        self._read(self.sender())

    @Slot()
    def _reply_finished(self):
        self._finished(self.sender())

    def _read(self, reply):
        if reply is None or self._reply is not reply:
            return
        if reply.attribute(_ATTR.HttpStatusCodeAttribute) != 200:
            reply.readAll()  # Discard any response body included in errors or redirects.
            return
        download = self._download
        received_before = download.received if download is not None else 0
        while reply.bytesAvailable():
            data = bytes(reply.read(_BLOCK))
            if not data:
                break
            if self._stage == "package":
                if download.received + len(data) > download.asset.size:
                    self._fail("size")
                    return
                if download.output.write(data) != len(data):
                    self._fail("write")
                    return
                download.received += len(data)
                download.sha256.update(data)
            else:
                limit = download.manifest.size if download is not None else _METADATA_LIMIT
                if len(self._body) + len(data) > limit:
                    self._fail("manifest" if download is not None else "release")
                    return
                self._body.extend(data)
        if self._stage == "package" and download.received != received_before:
            self.progress.emit(download.received, download.asset.size)

    def _finished(self, reply):
        if reply is None or self._reply is not reply:
            return
        status = reply.attribute(_ATTR.HttpStatusCodeAttribute)
        if status in _REDIRECT_STATUSES:
            redirect = reply.attribute(_ATTR.RedirectionTargetAttribute)
            if isinstance(redirect, QUrl) and self._stage != "release" and self._redirects < 4:
                target = reply.url().resolved(redirect)
                if _allowed_redirect(target):
                    self._redirects += 1
                    self._close_reply()
                    self._request(target)
                    return
            self._fail("unsafe_url")
            return
        error = reply.error()
        if error in {QNetworkReply.NetworkError.TimeoutError, QNetworkReply.NetworkError.OperationCanceledError}:
            self._fail("timeout")
            return
        if status != 200:
            self._fail("response" if status is not None else "network")
            return
        if error != QNetworkReply.NetworkError.NoError:
            self._fail("network")
            return
        self._read(reply)
        if self._reply is not reply:
            return
        self._close_reply()
        if self._stage == "release":
            self._finish_check()
        elif self._stage == "manifest":
            self._finish_manifest()
        else:
            self._finish_download()

    def _finish_check(self):
        try:
            release = parse_release(json.loads(self._body))
        except (UpdateMetadataError, json.JSONDecodeError, UnicodeError, RecursionError):
            self._fail("release")
            return
        self._reset()
        self.checked.emit(release)

    def _finish_manifest(self):
        download = self._download
        content = bytes(self._body)
        if len(content) != download.manifest.size or (
            download.manifest.digest and hashlib.sha256(content).hexdigest() != download.manifest.digest
        ):
            self._fail("manifest")
            return
        try:
            download.expected = expected_sha256(content, download.asset)
        except UpdateMetadataError:
            self._fail("manifest")
            return
        output = QTemporaryFile(str(download.destination.parent / ".snaploom-update-XXXXXX.part"))
        if not output.open():
            self._fail("write")
            return
        download.output = output
        self._body.clear()
        self._redirects = 0
        self._stage = "package"
        self._request(QUrl(download.asset.url))

    def _finish_download(self):
        download = self._download
        if download.received != download.asset.size:
            self._fail("size")
            return
        if download.sha256.hexdigest() != download.expected:
            self._fail("checksum")
            return
        if not download.output.flush():
            self._fail("write")
            return
        # QTemporaryFile.rename is atomic and fails if the destination appeared meanwhile.
        if not download.output.rename(str(download.destination)):
            self._fail("destination_exists" if os.path.lexists(download.destination) else "write")
            return
        download.output.setAutoRemove(False)
        path = download.destination
        self._reset()
        self.downloaded.emit(path)

    def _close_reply(self, *, abort=False):
        reply, self._reply = self._reply, None
        if reply is not None:
            reply.readyRead.disconnect(self._ready_read)
            reply.finished.disconnect(self._reply_finished)
            if abort and not reply.isFinished():
                reply.abort()
            reply.deleteLater()

    def _reset(self):
        download, self._download = self._download, None
        self._stage = ""
        self._body.clear()
        self._redirects = 0
        self._close_reply(abort=True)
        if download is not None and download.output is not None:
            download.output.close()
            if download.output.autoRemove():
                download.output.remove()

    def _fail(self, code):
        self._reset()
        self.failed.emit(code)
