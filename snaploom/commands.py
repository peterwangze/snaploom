"""Command-line entry points and local process handoff."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from . import VERSION
from .storage import data_directory


def arguments(values):
    parser = argparse.ArgumentParser(prog="snaploom", description="Capture, annotate, and pin screenshots.")
    parser.add_argument("--version", action="version", version=VERSION)
    parser.add_argument("--background", action="store_true", help="Start in the system tray.")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--smoke-test-ocr", action="store_true", help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="command")
    snip = commands.add_parser("snip", help="Start an interactive or scripted screenshot.")
    modes = snip.add_mutually_exclusive_group()
    for mode in ("full", "active-screen", "active-window", "last", "custom", "whiteboard"):
        modes.add_argument("--" + mode, action="store_true")
    modes.add_argument("--rect", "--area", nargs=4, type=int, metavar=("X", "Y", "WIDTH", "HEIGHT"))
    snip.add_argument("--size", nargs=2, type=int, metavar=("WIDTH", "HEIGHT"))
    snip.add_argument("--delay", type=float, default=0)
    snip.add_argument("--output", "-o", help="clipboard, pin, quick-save, ocr_clipboard or a file path")
    snip.add_argument("--hold", action="store_true", help="Open the editor before applying the output.")
    snip.add_argument("--block", nargs="?", type=float, const=60, help="Wait for a result when sent to a running app.")
    snip.add_argument("--shadow", action=argparse.BooleanOptionalAction, default=None)
    snip.add_argument("--keep-aspect-ratio", "--keep_aspect_ratio", action="store_true")
    snip.add_argument("--corner-radius", type=int, default=0)
    snip.add_argument("--transparent", action="store_true")
    snip.add_argument("--cursor", action="store_true")
    paste = commands.add_parser("paste", help="Pin a local image or the clipboard contents.")
    paste.add_argument("files", nargs="*")
    sources = paste.add_mutually_exclusive_group()
    sources.add_argument("--clipboard", action="store_true")
    sources.add_argument("--plain")
    sources.add_argument("--html")
    sources.add_argument("--files", nargs="+", dest="explicit_files")
    paste.add_argument("--pos", nargs=2, type=int, metavar=("X", "Y"))
    for name in ("show", "hide", "toggle-pins", "restore-pins", "settings", "history", "quit", "exit",
                 "toggle-images", "show-images", "hide-images", "switch-groups", "show-group-manager",
                 "switch-group-prev", "empty-group", "toggle-click-through", "no-click-through", "show-tray-menu",
                 "clear-snip-history", "disable-hotkeys", "enable-hotkeys", "toggle-hotkeys",
                 "disable-hot-corners", "enable-hot-corners", "toggle-hot-corners", "open-preferences", "docs",
                 "pick-color", "paste-as-image", "paste-as-plain-text"):
        commands.add_parser(name)
    for name in ("ocr", "barcode-scan", "barcode-decode"):
        reader = commands.add_parser(name)
        reader.add_argument("file", nargs="?")
        reader.add_argument("--block", nargs="?", type=float, const=60)
    for name in ("whiteboard", "transparent-whiteboard"):
        whiteboard = commands.add_parser(name)
        whiteboard.add_argument("--color")
        whiteboard.add_argument("--active-screen", action="store_true")
        whiteboard.add_argument("--size", nargs=2, type=int)
    for name in ("create-group", "switch-group"):
        group = commands.add_parser(name)
        group.add_argument("name", nargs="?")
    external = commands.add_parser("external")
    external.add_argument("argv", nargs=argparse.REMAINDER)
    parsed = parser.parse_args(values)
    if parsed.command == "snip":
        if not 0 <= parsed.delay <= 600:
            parser.error("--delay must be between 0 and 600 seconds.")
        if parsed.rect and min(parsed.rect[2:]) <= 0:
            parser.error("Capture width and height must be positive.")
    if getattr(parsed, "size", None) and min(parsed.size) <= 0:
        parser.error("Capture width and height must be positive.")
    if getattr(parsed, "block", None) is not None and not 0.1 <= parsed.block <= 600:
        parser.error("--block must be between 0.1 and 600 seconds.")
    data = vars(parsed)
    if data.get("explicit_files"):
        data["files"] = data.pop("explicit_files")
    if data.get("file"):
        data["file"] = str(Path(data["file"]).expanduser().resolve())
    if data.get("files"):
        data["files"] = [str(Path(file).expanduser().resolve()) for file in data["files"]]
    output = data.get("output")
    if output:
        data["output"] = resolve_outputs(output)
    return data


def resolve_outputs(output):
    reserved = {"pin", "clipboard", "quick-save", "silent", "ocr_clipboard", "file-dialog",
                "printer", "no-auto-save", "success", "copy-file"}
    parts = []
    cursor = 0
    while cursor < len(output):
        if output[cursor:].lstrip().startswith("exec("):
            end = output.find(");", cursor)
            end = len(output) if end < 0 else end + 1
        else:
            end = output.find(";", cursor)
            if end < 0:
                end = len(output)
        item = output[cursor:end].strip()
        if item:
            parts.append(item if item in reserved or item.startswith("exec(") else str(Path(item).expanduser().resolve()))
        cursor = end + 1
    return parts


def server_name():
    digest = hashlib.sha256(str(data_directory().resolve()).encode()).hexdigest()[:18]
    return f"snaploom-{digest}"


def send_to_instance(data):
    socket = QLocalSocket()
    socket.connectToServer(server_name())
    if not socket.waitForConnected(450):
        return None
    socket.write(json.dumps(data, ensure_ascii=False).encode() + b"\n")
    socket.flush()
    block = data.get("block")
    timeout = int(1000 * (block or 3))
    if not socket.canReadLine() and not socket.waitForReadyRead(timeout):
        if block:
            print("Timed out waiting for Snaploom.", file=sys.stderr)
            return 2
        return 0
    if socket.canReadLine():
        try:
            result = json.loads(bytes(socket.readLine()))
            if result.get("error"):
                print(result["error"], file=sys.stderr)
            if result.get("text"):
                print(result["text"])
            return int(result.get("code", 0))
        except (ValueError, TypeError):
            return 2
    return 2 if block else 0


class CommandServer:
    def __init__(self, handler):
        self.handler = handler
        self.server = QLocalServer()
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not self.server.listen(server_name()):
            QLocalServer.removeServer(server_name())
            if not self.server.listen(server_name()):
                raise OSError(self.server.errorString())
        self.server.newConnection.connect(self.accept)
        self.clients = set()

    def accept(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            self.clients.add(socket)
            socket.setReadBufferSize(65537)
            socket.readyRead.connect(lambda target=socket: self.receive(target))
            socket.disconnected.connect(lambda target=socket: self.disconnected(target))
            if socket.bytesAvailable():
                self.receive(socket)

    def disconnected(self, socket):
        self.clients.discard(socket)
        socket.deleteLater()

    def receive(self, socket):
        if socket.bytesAvailable() > 65536:
            socket.abort()
            return
        if not socket.canReadLine() or socket.property("handled"):
            return
        socket.setProperty("handled", True)
        try:
            data = json.loads(bytes(socket.readLine()))
            if not isinstance(data, dict):
                raise ValueError()
        except ValueError:
            self.reply(socket, {"code": 2, "error": "Invalid command."})
            return
        if data.get("block"):
            self.handler(data, lambda **result: self.reply(socket, result))
        else:
            self.reply(socket, {"code": 0})
            QTimer.singleShot(0, lambda: self.handler(data))

    def reply(self, socket, result):
        if socket in self.clients:
            socket.write(json.dumps(result, ensure_ascii=False).encode() + b"\n")
            socket.flush()
            socket.disconnectFromServer()
