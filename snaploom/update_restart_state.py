"""Save editable work across an update without changing the workspace database."""

from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from PySide6.QtCore import QByteArray, QPoint, QPointF, QRect, QTemporaryFile, QTimer
from PySide6.QtGui import QImage, QTextCursor
from PySide6.QtWidgets import QScrollArea
from shiboken6 import isValid

from .capture import ScreenFrame, Snapshot
from .editor import Annotation, ImageEditor
from .storage import png_bytes, rect_values


_FILENAME = "update-restart-drafts.zip"
_MAX_BYTES = 512 * 1024 * 1024


class RestartStateError(RuntimeError):
    """A restart snapshot could not safely be saved or restored."""


def _snapshot_path(app):
    return app.workspace.directory / _FILENAME


def _widget_geometry(widget):
    return bytes(widget.saveGeometry().toHex()).decode("ascii")


def _restore_geometry(widget, value):
    widget.restoreGeometry(QByteArray.fromHex(value.encode("ascii")))


def _save_canvas(canvas):
    document = canvas.document
    current = document.annotations
    undo = document.undo_history[:]
    redo = document.redo_history[:]
    # Capture the last positions of a stroke or move if it was interrupted before release.
    if canvas.drag_mode == "items" and canvas.working_annotations is not None:
        preview = canvas.working_annotations
    elif canvas.drag_mode == "annotation" and (len(canvas.points) > 1 or canvas.tool in {"pen", "marker"}):
        annotation = canvas._current_stroke()
        preview = (*current, annotation) if annotation is not None else current
    else:
        preview = current
    if preview != current:
        undo.append(current)
        current, redo = preview, []

    items = {}

    def annotation_ids(annotations):
        return [items.setdefault(item, len(items)) for item in annotations]

    document_states = {
        "current": annotation_ids(current),
        "undo": [annotation_ids(items) for items in undo],
        "redo": [annotation_ids(items) for items in redo],
    }
    return {
        "items": [asdict(item) for item in items],
        "document": document_states,
        "selection": rect_values(canvas.selection),
        "origin": [canvas._annotation_origin.x(), canvas._annotation_origin.y()],
        "tool": canvas.tool,
        "selected_items": sorted(canvas.selected_items),
        "width": canvas.annotation_width,
        "styles": canvas.annotation_styles,
        "next_numbers": canvas._next_numbers,
        "number_series": canvas._number_series,
        "line_points": [[point.x(), point.y()] for point in canvas.line_points],
        "line_redo": [[point.x(), point.y()] for point in canvas._line_redo],
        "aspect": canvas.aspect,
        "corner_radius": getattr(canvas, "_corner_radius", None),
    }


def restore_canvas(canvas, draft):
    """Restore edits, history and an unfinished polyline in the live editor."""
    items = [
        Annotation(**(item | {"points": tuple(map(tuple, item["points"]))}))
        for item in draft["items"]
    ]

    def annotations(indexes):
        return tuple(items[index] for index in indexes)

    document = draft["document"]
    canvas.document.annotations = annotations(document["current"])
    canvas.document.undo_history = [annotations(indexes) for indexes in document["undo"]]
    canvas.document.redo_history = [annotations(indexes) for indexes in document["redo"]]
    canvas._set_selection(QRect(*draft["selection"]))
    canvas._annotation_origin = QPoint(*draft["origin"])
    canvas.annotation_width = draft["width"]
    canvas.annotation_styles = draft["styles"]
    canvas.aspect = draft["aspect"]
    if draft["corner_radius"] is not None:
        canvas._corner_radius = draft["corner_radius"]
    canvas._next_numbers = draft["next_numbers"]
    canvas._number_series = draft["number_series"]
    canvas._fill_number_groups()
    canvas.number_spin.setValue(canvas._next_numbers[canvas._number_series])
    canvas._set_tool(draft["tool"] if draft["tool"] in canvas.tool_buttons else "select")
    canvas.selected_items = {i for i in draft["selected_items"] if 0 <= i < len(canvas.document.annotations)}
    canvas.line_points = [QPointF(*point) for point in draft["line_points"]]
    canvas._line_redo = [QPointF(*point) for point in draft["line_redo"]]
    canvas._render_key = None
    canvas._update_controls()
    canvas.update()


def _snapshot_images(snapshot, write_image):
    return {
        "geometry": rect_values(snapshot.geometry),
        "image": write_image(snapshot.image),
        "frames": [[rect_values(frame.geometry), write_image(frame.image)] for frame in snapshot.frames],
    }


def _pin_ids(app, windows):
    return [pin_id for pin_id, window in app.pin_windows.items() if window in windows]


def _compose_state(app, write_image):
    state = {
        "format": 1,
        "dashboard": {
            "visible": app.dashboard.isVisible(),
            "tab": app.dashboard.tabs.currentIndex(),
            "geometry": _widget_geometry(app.dashboard),
        },
        "visible_pins": [pin_id for pin_id, window in app.pin_windows.items() if window.isVisible()],
        "last_region": rect_values(app.last_region) if app.last_region is not None else None,
        "editors": [],
        "recognition": [],
    }
    editor = app.editor
    if editor is not None:
        context = app.capture_context
        # Resume editing; pending commands and their callbacks belong to the old process.
        options = {key: value for key, value in context.get("options", {}).items()
                   if key in {"whiteboard", "color_only", "shadow", "corner_radius", "cursor"}}
        state["capture"] = {
            "snapshot": _snapshot_images(editor.snapshot, write_image),
            "options": options,
            "canvas": _save_canvas(editor),
            "dashboard": bool(context.get("dashboard")),
            "hidden_pins": _pin_ids(app, context.get("pins", ())),
        }
    if app.scroll_dialog is not None:
        current, region, restore_dashboard, hidden = app.scroll_dialog.restart_scroll()
        state["scroll"] = {
            "image": write_image(current),
            "region": rect_values(region),
            "geometry": _widget_geometry(app.scroll_dialog),
            "dashboard": restore_dashboard,
            "hidden_pins": _pin_ids(app, hidden),
        }
    for dialog in app.dialogs:
        # A pin or its editor may be hidden temporarily while a capture is open.
        # The application removes a dialog from this set when it actually closes.
        if not isValid(dialog):
            continue
        if isinstance(dialog, ImageEditor):
            viewport = dialog.findChild(QScrollArea)
            state["editors"].append({
                "source": dialog.restart_source,
                "canvas": _save_canvas(dialog.canvas),
                "image": write_image(dialog.snapshot.image),
                "geometry": _widget_geometry(dialog),
                "scroll": [viewport.horizontalScrollBar().value(), viewport.verticalScrollBar().value()],
            })
        elif hasattr(dialog, "restart_text_editor"):
            edit = dialog.restart_text_editor
            cursor = edit.textCursor()
            state["recognition"].append({
                "text": edit.toPlainText(),
                "anchor": cursor.anchor(),
                "position": cursor.position(),
                "geometry": _widget_geometry(dialog),
                "capture_parent": editor is not None and dialog.parentWidget() is editor,
            })
    return state


def save_restart_state(app) -> Path:
    """Atomically publish the snapshot only after all unfinished work was written."""
    if app.capturing and app.editor is None and app.scroll_dialog is None:
        raise RestartStateError(app.tr("Wait for the screenshot to open, then try again.",
                                       "请等截图编辑器打开后重试。"))
    if app.recognition_jobs:
        raise RestartStateError(app.tr("Wait for recognition to finish, then try again.",
                                       "请等识别完成后重试。"))
    modal = app.qt.activeModalWidget()
    if modal is not None and modal is not app.update_dialog and not hasattr(modal, "restart_text_editor"):
        raise RestartStateError(app.tr("Finish the open dialog, then try again.",
                                       "请先完成当前对话框中的操作，再重试。"))

    destination = _snapshot_path(app)
    existing_draft = app.tr(
        f"A previous draft backup is still saved at {destination}. Restart Snaploom to retry recovery before updating.",
        f"上次的草稿仍备份在 {destination}。请先重启 Snaploom 重试恢复，再安装更新。",
    )
    if os.path.lexists(destination):
        raise RestartStateError(existing_draft)

    temporary = QTemporaryFile(str(destination.parent / ".restart-XXXXXX"))
    try:
        if not temporary.open():
            raise OSError(temporary.errorString())
        temporary.close()
        with Path(temporary.fileName()).open("w+b") as stream:
            count = total = 0
            with ZipFile(stream, "w") as archive:
                def write_image(image):
                    nonlocal count, total
                    count += 1
                    name = f"{count}.png"
                    content = png_bytes(image)
                    total += len(content)
                    if total > _MAX_BYTES:
                        raise ValueError("Too much image data to save for restart.")
                    archive.writestr(name, content)
                    return name, image.devicePixelRatio()

                manifest = json.dumps(_compose_state(app, write_image), ensure_ascii=False).encode("utf-8")
                if total + len(manifest) > _MAX_BYTES:
                    raise ValueError("Too much draft data to save for restart.")
                archive.writestr("drafts.json", manifest)
            stream.flush()
            os.fsync(stream.fileno())
        # Both paths are on the same volume, and rename never overwrites an older backup.
        if not temporary.rename(str(destination)):
            if os.path.lexists(destination):
                raise RestartStateError(existing_draft)
            raise OSError(temporary.errorString())
        temporary.setAutoRemove(False)
    except RestartStateError:
        raise
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as error:
        raise RestartStateError(app.tr(
            "Could not keep your drafts for restart. Check free space and try again.",
            "无法为重启保留草稿，请检查可用空间后重试。",
        )) from error
    finally:
        if temporary.autoRemove():
            temporary.remove()
    return destination


def discard_restart_state(app):
    _snapshot_path(app).unlink(missing_ok=True)


def _read_state(path):
    with ZipFile(path) as archive:
        if sum(item.file_size for item in archive.infolist()) > _MAX_BYTES:
            raise ValueError("Restart data is too large.")
        state = json.loads(archive.read("drafts.json"))
        if state["format"] != 1:
            raise ValueError("Unsupported restart data version.")

        def read_image(reference):
            name, dpr = reference
            image = QImage.fromData(archive.read(name), "PNG")
            if image.isNull():
                raise ValueError("A saved draft image is invalid.")
            image.setDevicePixelRatio(dpr)
            return image

        if "capture" in state:
            snapshot = state["capture"]["snapshot"]
            state["capture"]["snapshot"] = Snapshot(
                read_image(snapshot["image"]), QRect(*snapshot["geometry"]),
                tuple(ScreenFrame(QRect(*rect), read_image(ref)) for rect, ref in snapshot["frames"]),
            )
        if "scroll" in state:
            state["scroll"]["image"] = read_image(state["scroll"]["image"])
        for draft in state["editors"]:
            draft["image"] = read_image(draft["image"])
    return state


def _accessible_snapshot(app, snapshot, canvas):
    selection = QRect(*canvas["selection"]).translated(snapshot.geometry.topLeft())
    if selection.isEmpty():
        selection = snapshot.geometry
    screens = app.qt.screens()
    if screens and not any(screen.geometry().intersects(selection) for screen in screens):
        offset = app.qt.primaryScreen().availableGeometry().center() - selection.center()
        return Snapshot(
            snapshot.image, snapshot.geometry.translated(offset),
            tuple(ScreenFrame(frame.geometry.translated(offset), frame.image) for frame in snapshot.frames),
        )
    return snapshot


def _restore_state(app, state):
    dashboard = state["dashboard"]
    _restore_geometry(app.dashboard, dashboard["geometry"])
    app.dashboard.tabs.setCurrentIndex(dashboard["tab"])
    app.dashboard.setVisible(dashboard["visible"])
    app.last_region = QRect(*state["last_region"]) if state["last_region"] is not None else None
    visible = set(state["visible_pins"])
    for pin_id, window in app.pin_windows.items():
        window.setVisible(pin_id in visible)
        if pin_id in visible:
            window.recover_position()

    def hidden_pins(draft):
        return [app.pin_windows[pin_id] for pin_id in draft["hidden_pins"] if pin_id in app.pin_windows]

    if capture := state.get("capture"):
        options = capture["options"]
        app.capturing = True
        app.capture_context = {"dashboard": capture["dashboard"], "pins": hidden_pins(capture), "options": options}
        snapshot = _accessible_snapshot(app, capture["snapshot"], capture["canvas"])
        radius = options.get("corner_radius") or app.workspace.get("rounded_corners")
        app.open_capture_editor(snapshot, None, options, radius, draft=capture["canvas"])
    elif scrolling := state.get("scroll"):
        app.scroll_capture(scrolling["image"], QRect(*scrolling["region"]), None,
                           restore_dashboard=scrolling["dashboard"], hidden_pins=hidden_pins(scrolling))
        _restore_geometry(app.scroll_dialog, scrolling["geometry"])

    for draft in state["editors"]:
        source = draft["source"]
        pin_id = source.get("pin_id")
        if pin_id in app.pin_windows:
            dialog = app.open_pin_editor(pin_id, cropped=source["cropped"], draft_image=draft["image"])
        else:
            dialog = app.open_history_preview(draft["image"])
            if source.get("cropped"):
                dialog.activate_crop()
        _restore_geometry(dialog, draft["geometry"])
        restore_canvas(dialog.canvas, draft["canvas"])
        viewport = dialog.findChild(QScrollArea)
        x, y = draft["scroll"]
        QTimer.singleShot(0, viewport, lambda view=viewport, x=x, y=y: (
            view.horizontalScrollBar().setValue(x), view.verticalScrollBar().setValue(y),
        ))

    for draft in state["recognition"]:
        dialog = app.show_recognition_result(draft["text"], app.editor if draft["capture_parent"] else None)
        _restore_geometry(dialog, draft["geometry"])
        edit = dialog.restart_text_editor
        cursor = edit.textCursor()
        cursor.setPosition(draft["anchor"])
        cursor.setPosition(draft["position"], QTextCursor.MoveMode.KeepAnchor)
        edit.setTextCursor(cursor)


def restore_restart_state(app) -> bool:
    """Consume the snapshot only after restoring the user's work."""
    path = _snapshot_path(app)
    if not path.is_file():
        return False
    try:
        _restore_state(app, _read_state(path))
        discard_restart_state(app)
    except (BadZipFile, OSError, RuntimeError, ValueError, TypeError, KeyError, IndexError) as error:
        raise RestartStateError(app.tr(
            "Some work could not be reopened. Your draft backup is still saved.",
            "有些内容无法重新打开，草稿备份仍保存在本机。",
        )) from error
    return True
