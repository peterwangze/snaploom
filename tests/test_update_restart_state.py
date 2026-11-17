import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPointF, QRect, Qt
from PySide6.QtGui import QColor, QImage, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from snaploom.app import Application
from snaploom.capture import ScreenFrame, Snapshot
from snaploom.editor import Annotation, ImageEditor
from snaploom.storage import save_image
from snaploom import update_restart_state as restart
from snaploom.update_restart_state import RestartStateError, restore_restart_state, save_restart_state


class UpdateRestartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        if cls.qt.platformName() != "offscreen":
            raise unittest.SkipTest("Set QT_QPA_PLATFORM=offscreen to isolate staged restart windows.")
        cls.qt.setQuitOnLastWindowClosed(False)

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = Path(directory.name)
        self.app = None
        self.qt.clipboard().clear()
        self.addCleanup(self.qt.clipboard().clear)
        self.addCleanup(self.close_app)

    def open_app(self):
        self.app = Application(self.qt, self.folder, testing=True)
        self.app.workspace.set("detect_windows", False)
        return self.app

    def close_app(self):
        app, self.app = self.app, None
        if app is None:
            return
        for dialog in tuple(app.dialogs):
            dialog.reject()
        if app.editor is not None:
            app.editor.close()
        for window in app.pin_windows.values():
            window.hide()
            window.deleteLater()
        app.dashboard.hide()
        app.dashboard.deleteLater()
        app.shutdown()
        self.qt.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    @staticmethod
    def image(width=100, height=80, color="#a6ccd2"):
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        image.fill(QColor(color))
        return image

    def test_restart_reopens_files_with_editable_pin_history_and_ocr_drafts(self):
        app = self.open_app()
        app.workspace.set("restore_pins", False)
        app.workspace.set("annotation_width", 7)
        app.show_dashboard(1)
        path = save_image(self.image(), self.folder / "打开 试验.png")
        app.open_files([str(path)])
        pin_id, = app.pin_windows
        source_pixel = app.pin_windows[pin_id].image.pixelColor(10, 10)
        hidden_id = app.open_pin(self.image(color="#485693"))
        app.pin_action("hide", hidden_id)
        app.pin_action("annotate", pin_id)
        pin_draft, = app.dialogs
        self.assertIsInstance(pin_draft, ImageEditor)
        arrow = Annotation("arrow", ((8, 9), (34, 31)), color="#112355")
        note = Annotation("text", ((46, 13),), text="草稿 Keep editing")
        pin_draft.canvas._commit((arrow,))
        pin_draft.canvas._commit((arrow, note))
        pin_draft.canvas._undo()
        preview = app.open_history_preview(self.image(color="#2c4158"))
        preview.canvas._set_tool("line")
        preview.canvas.line_points = [QPointF(8, 9), QPointF(28, 27)]
        preview.canvas._line_redo = [QPointF(43, 44)]
        preview.hide()
        ocr = app.show_recognition_result("原始 OCR").restart_text_editor
        ocr.setPlainText("已编辑的 OCR 草稿")
        cursor = ocr.textCursor()
        cursor.setPosition(2)
        cursor.setPosition(6, QTextCursor.MoveMode.KeepAnchor)
        ocr.setTextCursor(cursor)

        recovery = save_restart_state(app)
        self.assertTrue(recovery.is_file())
        self.close_app()
        app = self.open_app()
        self.assertTrue(restore_restart_state(app))
        self.assertFalse(recovery.exists())
        self.assertFalse(restore_restart_state(app))
        self.assertEqual(app.workspace.get("annotation_width"), 7)
        self.assertFalse(app.workspace.get("restore_pins"))
        self.assertEqual(app.dashboard.tabs.currentIndex(), 1)
        self.assertTrue(app.dashboard.isVisible())
        self.assertTrue(app.pin_windows[pin_id].isVisible())
        self.assertFalse(app.pin_windows[hidden_id].isVisible())
        self.assertEqual(app.pin_windows[pin_id].image.pixelColor(10, 10), source_pixel)
        editor_dialogs = [dialog for dialog in app.dialogs if isinstance(dialog, ImageEditor)]
        pin_draft = next(dialog for dialog in editor_dialogs if dialog.restart_source.get("pin_id") == pin_id)
        self.assertEqual(pin_draft.canvas.document.annotations, (arrow,))
        self.assertTrue(pin_draft.canvas.undo_button.isEnabled())
        self.assertTrue(pin_draft.canvas.redo_button.isEnabled())
        pin_draft.canvas._redo()
        self.assertEqual(pin_draft.canvas.document.annotations, (arrow, note))
        pin_draft.accept()
        self.assertEqual([item["text"] for item in app.pin_windows[pin_id].state["annotations"]],
                         ["", "草稿 Keep editing"])
        preview = next(dialog for dialog in editor_dialogs if not dialog.restart_source)
        self.assertTrue(preview.isVisible())
        self.assertEqual(preview.canvas.tool, "line")
        self.assertEqual(preview.canvas.line_points, [QPointF(8, 9), QPointF(28, 27)])
        preview.canvas._redo()
        self.assertEqual(preview.canvas.line_points[-1], QPointF(43, 44))
        ocr, = [dialog.restart_text_editor for dialog in app.dialogs if hasattr(dialog, "restart_text_editor")]
        self.assertEqual(ocr.toPlainText(), "已编辑的 OCR 草稿")
        self.assertEqual((ocr.textCursor().anchor(), ocr.textCursor().position()), (2, 6))

    def test_restart_preserves_active_high_dpi_capture_selection_and_outputs(self):
        app = self.open_app()
        app.workspace.set("save_folder", str(self.folder / "exports"))
        app.workspace.set("hide_pins_on_capture", True)
        app.show_dashboard(1)
        pin_id = app.open_pin(self.image())
        physical = self.image(240, 180, "#c9396f")
        bounds = QRect(20, 30, 120, 90)
        snapshot = Snapshot(self.image(120, 90, "#c9396f"), bounds, (ScreenFrame(bounds, physical),))
        region = QRect(35, 44, 50, 35)
        with patch("snaploom.capture.capture_desktop", return_value=snapshot):
            app.capture({"rect": [35, 44, 50, 35]})
            QTest.qWait(160)
        self.assertTrue(app.capturing)
        self.assertFalse(app.pin_windows[pin_id].isVisible())
        self.assertEqual(app.editor.selected_geometry(), region)
        label = Annotation("text", ((6, 7),), text="Draft")
        app.editor._commit((label,))
        draft = save_restart_state(app)
        self.close_app()
        app = self.open_app()
        self.assertTrue(restore_restart_state(app))
        self.assertFalse(draft.exists())
        self.assertEqual(app.editor.selected_geometry(), region)
        self.assertEqual(app.editor.document.annotations, (label,))
        self.assertFalse(app.pin_windows[pin_id].isVisible())
        self.assertFalse(app.dashboard.isVisible())
        self.assertEqual(app.editor.export_image().size().toTuple(), (100, 70))
        QTest.keyClick(app.editor, Qt.Key.Key_Return)
        self.assertFalse(app.capturing)
        self.assertTrue(app.dashboard.isVisible())
        self.assertEqual(app.dashboard.tabs.currentIndex(), 1)
        self.assertTrue(app.pin_windows[pin_id].isVisible())
        exported, = (self.folder / "exports").glob("*.png")
        self.assertEqual(QImage(str(exported)).size().toTuple(), (100, 70))

    def test_restart_restores_pin_visibility_and_hide_order_after_window_close(self):
        app = self.open_app()
        opened = app.open_pin(self.image())
        second = app.open_pin(self.image())
        first = app.open_pin(self.image())
        other_group = app.open_pin(self.image(), state={"group": "Other"})
        app.pin_action("hide", first)
        app.pin_action("hide", second)
        app.workspace.set("restore_pins", False)
        save_restart_state(app)

        # QApplication closes open windows on normal exit. A pin's close event
        # persists a dismissal even though the user did not close that pin.
        app.pin_windows[opened].close()
        self.assertFalse(app.pin_windows[opened].state["visible"])
        self.assertEqual(app.closed_pins, [first, second, opened])
        self.close_app()
        app = self.open_app()
        self.assertTrue(restore_restart_state(app))

        persisted = {pin_id: state for pin_id, _, state in app.workspace.pins()}
        self.assertTrue(app.pin_windows[opened].isVisible())
        self.assertTrue(persisted[opened]["visible"])
        self.assertEqual(persisted[opened].get("hidden_at", 0), 0)
        self.assertTrue(persisted[other_group]["visible"])
        self.assertFalse(app.pin_windows[other_group].isVisible())
        self.assertEqual(app.closed_pins, [first, second])
        app.restore_last_pin()
        self.assertTrue(app.pin_windows[second].isVisible())
        self.assertFalse(app.pin_windows[first].isVisible())

    def test_restart_preserves_stitched_scroll_draft(self):
        app = self.open_app()
        app.workspace.set("save_folder", str(self.folder / "scrolled"))
        region = QRect(40, 50, 100, 80)
        combined = self.image(100, 140, "#8888df")
        with patch("snaploom.capture.capture_desktop", return_value=Snapshot(self.image(), region)), patch(
            "snaploom.app.stitch_images", return_value=combined,
        ):
            app.scroll_capture(self.image(), region, None, restore_dashboard=True)
            button = next(button for button in app.scroll_dialog.findChildren(QPushButton)
                          if button.text() == app.tr("Add next frame", "添加下一帧"))
            self.qt.processEvents()
            QTest.mouseClick(button, Qt.MouseButton.LeftButton)
            for _ in range(100):
                if app.scroll_dialog.restart_scroll()[0].size() == combined.size():
                    break
                QTest.qWait(10)
            self.assertEqual(app.scroll_dialog.restart_scroll()[0].size(), combined.size())
        save_restart_state(app)
        self.close_app()
        app = self.open_app()
        self.assertTrue(restore_restart_state(app))
        self.assertIsNotNone(app.scroll_dialog)
        button = next(button for button in app.scroll_dialog.findChildren(QPushButton)
                      if button.text() == app.tr("Save and Copy", "保存并复制"))
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        exported, = (self.folder / "scrolled").glob("*.png")
        self.assertEqual(QImage(str(exported)).size(), combined.size())
        self.assertEqual(QImage(str(exported)).pixelColor(4, 117), combined.pixelColor(4, 117))

    def test_interrupted_write_leaves_no_partial_archive(self):
        app = self.open_app()
        with patch("snaploom.update_restart_state.os.fsync", side_effect=OSError("disk denied")):
            with self.assertRaises(RestartStateError):
                save_restart_state(app)
        self.assertEqual(list(self.folder.glob(".restart-*")), [])
        self.assertFalse(restore_restart_state(app))

    def test_unrecovered_backup_is_retained_and_blocks_a_later_update(self):
        app = self.open_app()
        backup = save_restart_state(app)
        original = backup.read_bytes()
        with self.assertRaises(RestartStateError):
            save_restart_state(app)
        self.assertEqual(backup.read_bytes(), original)
        self.assertEqual(list(self.folder.glob(".restart-*")), [])
        backup.write_bytes(b"corrupt")
        with self.assertRaises(RestartStateError):
            restore_restart_state(app)
        self.assertEqual(backup.read_bytes(), b"corrupt")

    def test_archive_publication_preserves_a_backup_that_appeared_during_writing(self):
        app = self.open_app()
        compose = restart._compose_state
        backup = restart._snapshot_path(app)

        def write_competing_snapshot(*args):
            backup.write_bytes(b"previous draft")
            return compose(*args)

        with patch.object(restart, "_compose_state", side_effect=write_competing_snapshot):
            with self.assertRaises(RestartStateError):
                save_restart_state(app)
        self.assertEqual(backup.read_bytes(), b"previous draft")
        self.assertEqual(list(self.folder.glob(".restart-*")), [])

    def test_waits_for_unfinished_capture_or_recognition_before_exiting(self):
        app = self.open_app()
        app.capturing = True
        with self.assertRaises(RestartStateError):
            save_restart_state(app)
        app.capturing = False
        app.recognition_jobs[1] = object()
        with self.assertRaises(RestartStateError):
            save_restart_state(app)
