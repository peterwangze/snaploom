import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, QTimer, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QTabWidget

from snaploom.app import Application
from snaploom.capture import Snapshot
from snaploom.ui import Preferences


class AnnotationPreferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        if cls.qt.platformName() != "offscreen":
            raise unittest.SkipTest("Use QT_QPA_PLATFORM=offscreen to isolate annotation preferences.")
        cls.qt.setQuitOnLastWindowClosed(False)

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = Path(directory.name)
        self.application = None
        self.addCleanup(self.close_application)
        self.open_application()
        self.image = QImage(800, 600, QImage.Format.Format_RGB32)
        self.image.fill(Qt.GlobalColor.white)
        self.region = QRect(40, 40, 340, 240)
        self.enterContext(patch("snaploom.capture.capture_desktop", return_value=Snapshot(self.image, self.image.rect())))
        self.enterContext(patch("snaploom.capture.detect_windows", return_value=[]))

    def open_application(self):
        self.application = Application(self.qt, self.folder, testing=True)
        self.application.show_dashboard()
        self.qt.processEvents()

    def close_application(self):
        app = self.application
        if app is None:
            return
        if app.editor:
            app.editor.close()
        for dialog in tuple(app.dialogs):
            dialog.reject()
        for pin in app.pin_windows.values():
            pin.hide()
            pin.deleteLater()
        app.dashboard.hide()
        app.dashboard.deleteLater()
        app.shutdown()
        self.application = None
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    @staticmethod
    def click(widget):
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton)

    @staticmethod
    def type_width(editor, width):
        field = editor.stroke_width.lineEdit()
        QTest.mouseClick(field, Qt.MouseButton.LeftButton)
        QTest.keyClick(field, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClicks(field, str(width))
        QTest.keyClick(field, Qt.Key.Key_Return)

    def settings(self, *, width=None, remember=None, clear=False, save=True):
        original_exec = Preferences.exec
        errors = []

        def interact(dialog):
            def edit():
                try:
                    tabs = dialog.findChild(QTabWidget)
                    tabs.setCurrentIndex(tabs.indexOf(dialog.fields["annotation_width"].parentWidget()))
                    if width is not None:
                        field = dialog.fields["annotation_width"]
                        field.setValue(width)
                    if remember is not None:
                        field = dialog.fields["remember_annotation_styles"]
                        if field.isChecked() != remember:
                            QTest.mouseClick(field, Qt.MouseButton.LeftButton, pos=QPoint(7, field.height() // 2))
                    if clear:
                        field = dialog.clear_annotation_styles
                        self.assertTrue(field.isEnabled())
                        QTest.mouseClick(field, Qt.MouseButton.LeftButton, pos=QPoint(7, field.height() // 2))
                        self.assertTrue(field.isChecked())
                    buttons = dialog.findChild(QDialogButtonBox)
                    action = QDialogButtonBox.StandardButton.Save if save else QDialogButtonBox.StandardButton.Cancel
                    self.click(buttons.button(action))
                except Exception as error:
                    errors.append(error)
                    dialog.reject()

            QTimer.singleShot(0, edit)
            return original_exec(dialog)

        app = self.application
        with (
            patch.object(Preferences, "exec", interact),
            patch.object(app, "configure_hotkeys"),
            patch.object(app, "configure_super_snip"),
        ):
            app.settings()
        if errors:
            raise errors[0]

    def open_capture(self):
        app = self.application
        app.capture({"rect": self.region.getRect()})
        for _ in range(50):
            if app.editor is not None and app.editor.isVisible():
                return app.editor
            QTest.qWait(20)
        self.fail("Screenshot editor did not open.")

    def test_preferences_and_tool_width_are_shared_by_capture_pin_and_history(self):
        app = self.application
        self.settings(width=6)
        editor = self.open_capture()
        self.assertEqual(editor.stroke_width.value(), 6)
        self.type_width(editor, 8)
        self.assertTrue(editor.isVisible())
        self.click(editor.tool_buttons["pen"])
        self.assertEqual(editor.stroke_width.value(), 8)
        self.type_width(editor, 15)
        self.assertTrue(editor.isVisible())
        editor.close()

        pin_id = app.open_pin(self.image.copy(self.region))
        app.pin_action("annotate", pin_id)
        pin_editor, = app.dialogs
        self.click(pin_editor.canvas.tool_buttons["pen"])
        self.assertEqual(pin_editor.canvas.stroke_width.value(), 15)
        self.type_width(pin_editor.canvas, 19)
        self.assertTrue(pin_editor.isVisible())
        pin_editor.reject()

        app.workspace.remember(self.image, self.region, "copy")
        app.history_action("preview", app.workspace.history()[0][0])
        history_editor, = app.dialogs
        self.click(history_editor.canvas.tool_buttons["pen"])
        self.assertEqual(history_editor.canvas.stroke_width.value(), 19)
        history_editor.reject()

        self.close_application()
        self.open_application()
        editor = self.open_capture()
        self.assertEqual(editor.stroke_width.value(), 8)
        self.click(editor.tool_buttons["pen"])
        self.assertEqual(editor.stroke_width.value(), 19)

    def test_disabling_remembrance_and_clearing_saved_styles_follow_save_and_cancel(self):
        app = self.application
        editor = self.open_capture()
        self.click(editor.tool_buttons["pen"])
        self.type_width(editor, 16)
        editor.close()

        self.settings(clear=True, save=False)
        self.assertEqual(app.workspace.get("annotation_styles")["pen"]["width"], 16)
        self.settings(remember=False)
        editor = self.open_capture()
        self.click(editor.tool_buttons["pen"])
        self.assertEqual(editor.stroke_width.value(), 3)
        self.type_width(editor, 22)
        editor.close()
        self.assertEqual(app.workspace.get("annotation_styles")["pen"]["width"], 16)

        self.settings(remember=True, clear=True)
        self.assertEqual(app.workspace.get("annotation_styles"), {})
        editor = self.open_capture()
        self.click(editor.tool_buttons["pen"])
        self.assertEqual(editor.stroke_width.value(), 3)
