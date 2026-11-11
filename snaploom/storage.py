"""Local preferences, screenshot history and pinned image state."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRect, QSaveFile, QStandardPaths
from PySide6.QtGui import QImage, QImageWriter


DEFAULTS = {
    "history_limit": 50,
    "save_folder": "",
    "save_pattern": "Snaploom_%Y-%m-%d_%H-%M-%S",
    "format": "png",
    "auto_save": False,
    "copy_after_save": False,
    "include_cursor": False,
    "run_at_login": False,
    "start_hidden": False,
    "allow_remote_images": False,
    "hotkeys": {
        "capture": "F1", "paste": "F3", "toggle_pins": "Shift+F3",
        "repeat": "Shift+F1", "click_through": "", "active": "",
    },
    "default_pin_opacity": 100,
    "hide_pins_on_capture": False,
    "restore_pins": True,
    "show_grid": False,
    "color_format": "HEX",
    "rounded_corners": 0,
    "capture_shadow": False,
    "show_magnifier": True,
    "detect_windows": True,
    "editor_shortcuts": {},
    "ocr_provider": "local",
    "hot_corner": "",
    "super_snip": "",
    "custom_commands": {},
}


def data_directory() -> Path:
    if location := os.environ.get("SNAPLOOM_DATA_DIR"):
        return Path(location).expanduser().resolve()
    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))


def pictures_directory() -> Path:
    location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)
    return (Path(location) if location else Path.home() / "Pictures") / "Snaploom"


def png_bytes(image: QImage) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise ValueError("Could not encode the image.")
    return bytes(data)


def rect_values(rect: QRect) -> list[int]:
    return [rect.x(), rect.y(), rect.width(), rect.height()]


def save_image(image: QImage, path: Path) -> Path:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    target = QSaveFile(str(path))
    if not target.open(QIODevice.OpenModeFlag.WriteOnly):
        raise OSError(target.errorString())
    kind = path.suffix.lstrip(".").lower() or "png"
    writer = QImageWriter(target, b"jpeg" if kind == "jpg" else kind.encode("ascii"))
    if kind in {"jpeg", "jpg", "webp"}:
        writer.setQuality(92)
    if not writer.write(image):
        target.cancelWriting()
        raise OSError(writer.errorString())
    if not target.commit():
        raise OSError(target.errorString())
    return path


class Workspace:
    def __init__(self, directory: Path | None = None):
        self.directory = directory or data_directory()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.directory / "workspace.sqlite3")
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA secure_delete=ON;
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY, created REAL NOT NULL, image BLOB NOT NULL,
                region TEXT NOT NULL, action TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pins (
                id INTEGER PRIMARY KEY, image BLOB NOT NULL, state TEXT NOT NULL
            );
        """)

    def get(self, key):
        row = self.db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        value = json.loads(row[0]) if row else DEFAULTS.get(key)
        if key == "hotkeys":
            return DEFAULTS[key] | (value or {})
        return value

    def set(self, key, value):
        with self.db:
            self.db.execute(
                "INSERT INTO settings VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value, ensure_ascii=False)),
            )
        if key == "history_limit":
            self.trim_history(int(value))

    def remember(self, image: QImage, rect: QRect, action: str) -> None:
        limit = int(self.get("history_limit"))
        if limit <= 0:
            return
        with self.db:
            self.db.execute(
                "INSERT INTO history(created, image, region, action) VALUES (?, ?, ?, ?)",
                (time.time(), png_bytes(image), json.dumps(rect_values(rect)), action),
            )
            self._trim(limit)

    def _trim(self, limit):
        self.db.execute(
            "DELETE FROM history WHERE id IN (SELECT id FROM history ORDER BY id DESC LIMIT -1 OFFSET ?)",
            (max(0, limit),),
        )

    def trim_history(self, limit):
        with self.db:
            self._trim(limit)
        self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def clear_history(self):
        self.trim_history(0)

    def delete_history(self, item_id):
        with self.db:
            self.db.execute("DELETE FROM history WHERE id = ?", (item_id,))
        self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def history(self):
        return self.db.execute(
            "SELECT id, created, region, action FROM history ORDER BY id DESC"
        ).fetchall()

    def history_image(self, item_id):
        row = self.db.execute("SELECT image FROM history WHERE id = ?", (item_id,)).fetchone()
        return QImage.fromData(row[0]) if row else QImage()

    def regions(self) -> list[QRect]:
        return [QRect(*json.loads(row[2])) for row in self.history() if row[2] != "[0, 0, 0, 0]"]

    def add_pin(self, image, state):
        with self.db:
            cursor = self.db.execute("INSERT INTO pins(image, state) VALUES (?, ?)", (png_bytes(image), json.dumps(state)))
        return cursor.lastrowid

    def update_pin(self, pin_id, state, image=None):
        with self.db:
            if image is None:
                self.db.execute("UPDATE pins SET state = ? WHERE id = ?", (json.dumps(state), pin_id))
            else:
                self.db.execute(
                    "UPDATE pins SET state = ?, image = ? WHERE id = ?",
                    (json.dumps(state), png_bytes(image), pin_id),
                )

    def delete_pin(self, pin_id):
        with self.db:
            self.db.execute("DELETE FROM pins WHERE id = ?", (pin_id,))

    def pins(self):
        for pin_id, image, state in self.db.execute("SELECT id, image, state FROM pins ORDER BY id"):
            yield pin_id, QImage.fromData(image), json.loads(state)

    def close(self):
        self.db.close()
