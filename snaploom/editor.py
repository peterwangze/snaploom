"""Region selection and non-destructive annotation for frozen screen captures."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from math import atan2, cos, hypot, pi, sin

from PySide6.QtCore import QPoint, QPointF, QRunnable, QRect, QRectF, QSize, QThreadPool, QTimer, Qt, Signal
from PySide6.QtGui import (
    QColor, QCursor, QFont, QFontMetricsF, QIcon, QImage, QKeySequence, QPainter,
    QPainterPath, QPen, QPixmap, QPolygonF, QRegion, QShortcut, QTransform,
)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
    QFontDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QMenu, QScrollArea, QSpinBox,
    QStyle, QToolButton, QVBoxLayout, QWidget,
)


TOOLS = (
    ("select", "Select and move", "选取和移动", "V"),
    ("rectangle", "Rectangle", "矩形", "R"),
    ("ellipse", "Ellipse", "椭圆", "E"),
    ("line", "Line strip", "多段线", "L"),
    ("arrow", "Arrow", "箭头", "A"),
    ("pen", "Pencil", "画笔", "B"),
    ("marker", "Highlighter", "荧光笔", "H"),
    ("text", "Text", "文字", "T"),
    ("number", "Step number", "步骤序号", "N"),
    ("mosaic", "Mosaic", "马赛克", "M"),
    ("blur", "Gaussian blur", "高斯模糊", "G"),
    ("redact", "Solid redaction", "实色遮盖", "D"),
    ("erase", "Restore original pixels", "橡皮擦", "X"),
    ("magnify", "Magnify region", "区域放大", "Z"),
)
DEFAULT_EDITOR_SHORTCUTS = {tool: key for tool, _, _, key in TOOLS} | {
    "copy": "Ctrl+C",
    "save": "Ctrl+S",
    "pin": "Ctrl+P",
    "quick-save": "Ctrl+Shift+S",
    "copy-file": "Ctrl+Shift+C",
    "refresh": "F5",
    "color": "C",
}
REGION_TOOLS = {"rectangle", "ellipse", "mosaic", "blur", "redact", "erase", "magnify"}
EFFECT_TOOLS = {"mosaic", "blur", "erase", "magnify"}
DEFAULT_COLOR = "#ff5b55"
ACCENT = QColor("#68bfff")


@dataclass(frozen=True)
class Annotation:
    tool: str
    points: tuple
    color: str = DEFAULT_COLOR
    width: int = 3
    text: str = ""
    fill: bool = False
    style: str = "default"
    angle: float = 0
    background: str = ""
    outline: str = ""
    outline_width: float = 1
    font_family: str = ""
    series: str = ""

    def font(self):
        font = QFont(self.font_family) if self.font_family else QFont()
        font.setPixelSize(max(14, self.width * 5))
        font.setBold(self.style == "bold")
        return font

    def bounds(self):
        first = self.points[0]
        if self.tool == "number":
            radius = max(10, self.width * 4)
            return QRectF(first[0] - radius, first[1] - radius, radius * 2, radius * 2)
        if self.tool == "text":
            metrics = QFontMetricsF(self.font())
            lines = self.text.splitlines() or [""]
            width = max(metrics.horizontalAdvance(line) for line in lines) + 8
            return QRectF(first[0], first[1], width, metrics.lineSpacing() * len(lines) + 6)
        xs, ys = zip(*self.points)
        return QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

    def transform(self):
        center = self.bounds().center()
        return QTransform().translate(center.x(), center.y()).rotate(self.angle).translate(-center.x(), -center.y())

    def visual_bounds(self):
        rect = self.bounds()
        return self.transform().mapRect(rect) if self.angle else rect

    def moved(self, delta):
        return replace(self, points=tuple((x + delta.x(), y + delta.y()) for x, y in self.points))

    def scaled(self, multiplier):
        center = self.bounds().center()
        return replace(
            self,
            points=tuple(
                (
                    center.x() + (x - center.x()) * multiplier,
                    center.y() + (y - center.y()) * multiplier,
                )
                for x, y in self.points
            ),
        )


class AnnotationDocument:
    def __init__(self, annotations=()):
        self.annotations = tuple(annotations)
        self.undo_history = []
        self.redo_history = []

    def commit(self, annotations):
        updated = tuple(annotations)
        if updated == self.annotations:
            return
        self.undo_history.append(self.annotations)
        self.annotations = updated
        self.redo_history.clear()

    def undo(self):
        if self.undo_history:
            self.redo_history.append(self.annotations)
            self.annotations = self.undo_history.pop()

    def redo(self):
        if self.redo_history:
            self.undo_history.append(self.annotations)
            self.annotations = self.redo_history.pop()


def _image_region(image, rect, logical_size):
    scale_x = image.width() / logical_size.width()
    scale_y = image.height() / logical_size.height()
    return QRectF(
        rect.x() * scale_x, rect.y() * scale_y, rect.width() * scale_x, rect.height() * scale_y
    ).toAlignedRect().intersected(image.rect())


def _blur_image(image, radius):
    from PIL import Image, ImageFilter

    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    width, height = rgba.width(), rgba.height()
    pixels = Image.frombytes("RGBA", (width, height), bytes(rgba.constBits()))
    blurred = pixels.filter(ImageFilter.GaussianBlur(radius=radius))
    return QImage(blurred.tobytes(), width, height, width * 4, QImage.Format.Format_RGBA8888).copy()


def _paint_effect(painter, original, composited, annotation, logical_size):
    rect = annotation.bounds()
    physical = _image_region(original, rect, logical_size)
    if physical.isEmpty():
        return
    source = original if annotation.tool in {"erase", "magnify"} else composited
    if annotation.tool == "magnify":
        center = rect.center()
        smaller = QRectF(
            center.x() - rect.width() / 4, center.y() - rect.height() / 4,
            rect.width() / 2, rect.height() / 2,
        )
        physical = _image_region(original, smaller, logical_size)
        if physical.isEmpty():
            return
    patch = source.copy(physical)
    patch.setDevicePixelRatio(1.0)
    scale = original.width() / logical_size.width()
    if annotation.tool == "mosaic":
        size = max(3, round(annotation.width * 3 * scale))
        patch = patch.scaled(
            max(1, round(patch.width() / size)), max(1, round(patch.height() / size)),
            Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation,
        )
    elif annotation.tool == "blur":
        patch = _blur_image(patch, max(1, annotation.width * 1.5 * scale))
    painter.save()
    if annotation.style == "ellipse":
        path = QPainterPath()
        path.addEllipse(rect)
        painter.setClipPath(path, Qt.ClipOperation.IntersectClip)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, annotation.tool != "mosaic")
    painter.drawImage(rect, patch)
    painter.restore()
    if annotation.tool == "magnify":
        if annotation.style == "ellipse":
            painter.drawEllipse(rect)
        else:
            painter.drawRect(rect)


def _paint_arrowhead(painter, start, tip, width, open_head):
    direction = tip - start
    length = hypot(direction.x(), direction.y())
    if length < 1:
        return
    axis = direction / length
    normal = QPointF(-axis.y(), axis.x())
    size = min(length, max(10, width * 4))
    left = tip - axis * size + normal * (size * 0.46)
    right = tip - axis * size - normal * (size * 0.46)
    if open_head:
        painter.drawPolyline(QPolygonF([left, tip, right]))
    else:
        painter.setBrush(painter.pen().color())
        painter.drawPolygon(QPolygonF([tip, left, right]))


def paint_annotation(painter, original, annotation, composited=None, logical_size=None):
    painter.save()
    if annotation.angle:
        painter.setTransform(annotation.transform(), True)
    _paint_annotation(painter, original, annotation, composited, logical_size)
    painter.restore()


def _paint_annotation(painter, original, annotation, composited, logical_size):
    color = QColor(annotation.color)
    width = annotation.width
    pen = QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    if annotation.style == "dashed":
        pen.setStyle(Qt.PenStyle.DashLine)
    elif annotation.style == "dotted":
        pen.setStyle(Qt.PenStyle.DotLine)
    painter.setPen(pen)
    painter.setBrush(color if annotation.fill else Qt.BrushStyle.NoBrush)
    if annotation.tool in EFFECT_TOOLS:
        _paint_effect(painter, original, composited if composited is not None else original, annotation, logical_size)
        return
    rect = annotation.bounds()
    if annotation.tool == "redact":
        color.setAlpha(255)
        if annotation.style == "ellipse":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(rect)
        else:
            painter.fillRect(rect, color)
        return
    if annotation.tool == "rectangle":
        if annotation.style == "rounded":
            painter.drawRoundedRect(rect, 5, 5)
        else:
            painter.drawRect(rect)
        return
    if annotation.tool == "ellipse":
        painter.drawEllipse(rect)
        return
    if annotation.tool == "number":
        font = QFont()
        font.setPixelSize(max(12, width * 4))
        font.setBold(True)
        painter.setFont(font)
        painter.setBrush(color)
        painter.drawEllipse(rect)
        painter.setPen(QColor("#0e1824") if color.lightness() > 165 else QColor("#ffffff"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, annotation.text)
        return
    if annotation.tool == "text":
        font = annotation.font()
        painter.setFont(font)
        if annotation.background:
            painter.fillRect(rect, QColor(annotation.background))
        if annotation.outline:
            metrics = QFontMetricsF(font)
            path = QPainterPath()
            baseline = rect.y() + 2 + metrics.ascent()
            for line in annotation.text.splitlines():
                path.addText(QPointF(rect.x() + 2, baseline), font, line)
                baseline += metrics.lineSpacing()
            painter.setPen(QPen(QColor(annotation.outline), annotation.outline_width))
            painter.setBrush(color)
            painter.drawPath(path)
            return
        painter.drawText(rect.adjusted(2, 2, 2, 2), Qt.AlignmentFlag.AlignLeft, annotation.text)
        return
    points = [QPointF(x, y) for x, y in annotation.points]
    if annotation.tool == "marker":
        color.setAlpha(round(90 * color.alphaF()))
        if annotation.style in {"rectangle", "ellipse"}:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            if annotation.style == "ellipse":
                painter.drawEllipse(rect)
            else:
                painter.drawRect(rect)
            return
        pen.setColor(color)
        pen.setWidth(max(8, width * 4))
        painter.setPen(pen)
    if len(points) == 1:
        painter.drawPoint(points[0])
    else:
        path = QPainterPath(points[0])
        for point in points[1:]:
            path.lineTo(point)
        painter.drawPath(path)
        if annotation.tool == "arrow":
            if annotation.style != "reverse":
                _paint_arrowhead(painter, points[-2], points[-1], width, annotation.style == "open")
            if annotation.style in {"double", "reverse"}:
                _paint_arrowhead(painter, points[1], points[0], width, False)


def render_annotations(image, annotations, corner_radius=0, logical_size=None):
    """Compose output in the input image's logical coordinate system."""
    if image.isNull():
        return QImage()
    logical_size = logical_size or image.deviceIndependentSize()
    result = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    scale_x = image.deviceIndependentSize().width() / logical_size.width()
    scale_y = image.deviceIndependentSize().height() / logical_size.height()
    for annotation in annotations:
        previous = result if annotation.tool in {"mosaic", "blur"} else None
        if previous is not None:
            result = result.copy()
        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(scale_x, scale_y)
        paint_annotation(painter, image, annotation, previous, logical_size)
        painter.end()
    if not corner_radius:
        return result
    rounded = QImage(result.size(), QImage.Format.Format_ARGB32_Premultiplied)
    rounded.fill(Qt.GlobalColor.transparent)
    rounded.setDevicePixelRatio(result.devicePixelRatio())
    if result.colorSpace().isValid():
        rounded.setColorSpace(result.colorSpace())
    painter = QPainter(rounded)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(QPointF(), result.deviceIndependentSize()), corner_radius * scale_x, corner_radius * scale_y)
    painter.fillPath(path, Qt.GlobalColor.white)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.drawImage(QPointF(), result)
    painter.end()
    return rounded


def _read_option(options, key, default=None):
    if isinstance(options, Mapping):
        return options.get(key, default)
    return getattr(options, key, default)


def _point_tuple(point):
    return point.x(), point.y()


def _native_tool_icon(tool, color):
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(color, 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    if tool == "select":
        painter.drawPolygon(QPolygonF([QPointF(5, 3), QPointF(18, 13), QPointF(12, 14), QPointF(9, 20)]))
    elif tool in {"rectangle", "redact"}:
        if tool == "redact":
            painter.setBrush(color)
        painter.drawRoundedRect(QRectF(4, 6, 16, 12), 2, 2)
    elif tool == "ellipse":
        painter.drawEllipse(QRectF(4, 6, 16, 12))
    elif tool == "line":
        painter.drawPolyline(QPolygonF([QPointF(4, 17), QPointF(10, 7), QPointF(15, 14), QPointF(20, 5)]))
    elif tool == "arrow":
        painter.drawLine(QPointF(5, 19), QPointF(19, 5))
        painter.drawPolyline(QPolygonF([QPointF(11, 5), QPointF(19, 5), QPointF(19, 13)]))
    elif tool in {"pen", "marker"}:
        painter.drawPolygon(QPolygonF([QPointF(5, 20), QPointF(6, 14), QPointF(16, 4), QPointF(20, 8), QPointF(10, 18)]))
        if tool == "marker":
            painter.drawLine(4, 21, 20, 21)
    elif tool in {"text", "number"}:
        font = QFont()
        font.setBold(True)
        font.setPixelSize(16 if tool == "text" else 12)
        painter.setFont(font)
        if tool == "number":
            painter.drawEllipse(QRectF(4, 4, 16, 16))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "T" if tool == "text" else "1")
    elif tool in {"mosaic", "blur"}:
        for row in range(3):
            for column in range(3):
                shade = QColor(color)
                shade.setAlpha(210 if (column + row) % 2 else 70)
                tile = QRectF(5 + column * 5, 5 + row * 5, 4, 4)
                if tool == "mosaic":
                    painter.fillRect(tile, shade)
                else:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(shade)
                    painter.drawEllipse(tile)
    elif tool == "erase":
        painter.drawPolygon(QPolygonF([QPointF(4, 14), QPointF(14, 4), QPointF(20, 10), QPointF(12, 19), QPointF(8, 19)]))
        painter.drawLine(9, 9, 15, 15)
        painter.drawLine(12, 19, 20, 19)
    elif tool == "magnify":
        painter.drawEllipse(QRectF(4, 4, 11, 11))
        painter.drawLine(13, 13, 20, 20)
        painter.drawLine(7, 9, 12, 9)
        painter.drawLine(9, 7, 9, 12)
    painter.end()
    return QIcon(pixmap)


class CaptureEditor(QWidget):
    completed = Signal(str, QImage, QRect)
    cancelled = Signal()
    refresh_requested = Signal()
    custom_output = Signal(str, QImage, QRect)
    color_copied = Signal(str)
    _elements_detected = Signal(int, object)

    def __init__(self, snapshot, *, selection=None, windows=(), options=None, history=(), parent=None):
        self._embedded = _read_option(options, "_embedded", False)
        flags = Qt.WindowType.Widget if self._embedded else (
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        )
        super().__init__(parent, flags)
        self.options = options
        self._chinese = str(_read_option(options, "locale", "en")).lower().startswith("zh")
        self.color_only = bool(_read_option(options, "color_only", False))
        self.setWindowTitle(self._label("Pick a color", "屏幕取色") if self.color_only else self._label(
            "Capture and annotate", "截图与标注",
        ))
        self.setAccessibleName(self.windowTitle())
        self.setAccessibleDescription(
            self._label("Click or press Enter to copy the color at the pointer. Escape cancels.",
                        "单击或按回车复制指针位置的颜色。按 Esc 取消。")
            if self.color_only else self._label(
                "Drag to select an area. Hold Alt to magnify. Press Space to start and finish a keyboard selection. Enter copies the capture.",
                "拖动选择区域，按住 Alt 查看放大镜。可按空格开始和结束键盘框选。按回车复制截图。",
            )
        )
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.snapshot = snapshot
        self._window_bounds = [(QRect(rect), str(title)) for rect, title in windows]
        self.windows = self._window_bounds
        self.history = [QRect(rect) for rect in history]
        self.history_index = len(self.history)
        self.selection = QRect()
        self._keyboard_anchor = None
        self.hover_index = -1
        self.position = QPoint(-1, -1)
        self.drag_start = QPoint()
        self.original_selection = QRect()
        self.drag_mode = ""
        self.resize_edges = ()
        self.original_annotations = ()
        self.selected_items = set()
        self.working_annotations = None
        self.points = []
        self.line_points = []
        self.tool = "select"
        self.document = AnnotationDocument(_read_option(options, "_annotations", ()))
        self._number_series = ""
        self._next_numbers = {"": 1}
        for item in self.document.annotations:
            if item.tool == "number" and item.text.isdecimal():
                self._next_numbers[item.series] = min(9999, max(self._next_numbers.get(item.series, 1), int(item.text) + 1))
        self.color = DEFAULT_COLOR
        self.show_magnifier = self.color_only or _read_option(options, "show_magnifier", True)
        self._magnifying = False
        self.show_guides = _read_option(options, "show_guides", True)
        self.auto_detect = not self.color_only and _read_option(options, "auto_detect", True)
        self._detect_elements = _read_option(options, "detect_elements")
        self._probe_generation = 0
        self._probe_active = False
        self._probe_timer = QTimer(self)
        self._probe_timer.setSingleShot(True)
        self._probe_timer.setInterval(105)
        self._probe_timer.timeout.connect(self._probe_elements)
        self._elements_detected.connect(self._receive_elements, Qt.ConnectionType.QueuedConnection)
        self.crop_mode = False
        aspect = _read_option(options, "aspect_ratio", 0)
        self.aspect = self._aspect_value(aspect)
        self.finished = False
        self._render_key = None
        self._rendered = QImage()
        self._base_selection = QRect()
        self._base_image = QImage()
        custom_shortcuts = _read_option(options, "shortcuts", {}) or {}
        self._shortcut_sequences = {
            action: QKeySequence.fromString(str(custom_shortcuts.get(action, default)), QKeySequence.SequenceFormat.PortableText)
            for action, default in DEFAULT_EDITOR_SHORTCUTS.items()
        }
        self._bind_shortcuts()
        if self._embedded:
            self.setFixedSize(snapshot.geometry.size())
        else:
            self.setGeometry(snapshot.geometry)
        self.toolbar = self._build_toolbar()
        if selection is not None and not self.color_only:
            self.selection = self._local_rect(selection)
        self._place_toolbar()

    def _label(self, english, chinese):
        return chinese if self._chinese else english

    def _shortcut_label(self, action):
        return self._shortcut_sequences[action].toString(QKeySequence.SequenceFormat.NativeText)

    def _bind_shortcuts(self):
        actions = {
            tool: lambda name=tool: self._set_tool(name) if not self.selection.isEmpty() and not self.crop_mode else None
            for tool, _, _, _ in TOOLS
        }
        if not self._embedded:
            actions |= {
                action: lambda name=action: self._complete(name)
                for action in ("copy", "save", "pin", "quick-save", "copy-file")
            }
            actions["refresh"] = self.refresh_requested.emit
            actions["color"] = self._copy_color
        if self.color_only:
            actions = {"color": self._copy_color, "refresh": self.refresh_requested.emit}
        bindings = {}
        for action in actions:
            sequence = self._shortcut_sequences[action]
            if sequence.isEmpty() or sequence.toString() in bindings:
                continue
            shortcut = QShortcut(sequence, self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(actions[action])
            bindings[sequence.toString()] = shortcut
        self._bound_shortcuts = bindings

    @staticmethod
    def _aspect_value(value):
        if isinstance(value, (list, tuple)) and len(value) == 2:
            return float(value[0]) / float(value[1]) if value[1] else 0
        return float(value or 0)

    def _button(self, row, title, callback, *, icon=None, shortcut=None, checkable=False):
        button = QToolButton(self.toolbar)
        if icon is None:
            button.setText(title)
        else:
            button.setIcon(icon)
            button.setIconSize(QSize(22, 22))
        button.setAccessibleName(title)
        button.setToolTip(f"{title} ({shortcut})" if shortcut else title)
        button.setCheckable(checkable)
        button.clicked.connect(callback)
        button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        row.addWidget(button)
        return button

    def _build_toolbar(self):
        toolbar = QFrame(self)
        self.toolbar = toolbar
        toolbar.setObjectName("captureToolbar")
        toolbar.setAccessibleName(self._label("Capture toolbar", "截图工具栏"))
        toolbar.setStyleSheet(
            "QFrame#captureToolbar { background: #172334; color: #f4f7fc; border: 1px solid #52647b; border-radius: 11px; }"
            "QToolButton { color: #f4f7fc; background: transparent; border: 1px solid transparent; border-radius: 5px;"
            " padding: 5px 7px; min-height: 20px; }"
            "QToolButton:hover, QToolButton:checked { background: #2f4d68; }"
            "QToolButton:focus, QComboBox:focus, QSpinBox:focus { border: 1px solid #91d3ff; }"
            "QToolButton#copyCapture { background: #b7e2ff; color: #0d2a44; font-weight: 600; }"
            "QToolButton:disabled { color: #8e9cb1; }"
            "QLabel, QCheckBox { color: #f4f7fc; }"
            "QComboBox, QSpinBox { color: #f4f7fc; background: #233449; border: 1px solid #52647b;"
            " border-radius: 4px; padding: 2px 3px; }"
            "QComboBox QAbstractItemView { color: #f4f7fc; background: #233449; }"
        )
        layout = QVBoxLayout(toolbar)
        layout.setContentsMargins(7, 6, 7, 6)
        layout.setSpacing(4)
        tools_row = QHBoxLayout()
        tools_row.setSpacing(0)
        layout.addLayout(tools_row)
        self.tool_buttons = {}
        for key, english, chinese, _ in TOOLS:
            button = self._button(
                tools_row, self._label(english, chinese), lambda checked=False, name=key: self._set_tool(name),
                icon=_native_tool_icon(key, QColor("#f3f7ff")), shortcut=self._shortcut_label(key), checkable=True,
            )
            self.tool_buttons[key] = button
        self.tool_buttons["select"].setChecked(True)
        controls = QHBoxLayout()
        controls.setSpacing(3)
        layout.addLayout(controls)
        self.color_button = self._button(controls, self._label("Color", "颜色"), self._choose_color)
        self._update_color_button()
        self.stroke_width = QSpinBox(toolbar)
        self.stroke_width.setRange(1, 48)
        self.stroke_width.setValue(3)
        self.stroke_width.setPrefix(self._label("Width ", "粗细 "))
        self.stroke_width.setAccessibleName(self._label("Stroke width or effect strength", "线宽或效果强度"))
        self.stroke_width.valueChanged.connect(self._style_changed)
        controls.addWidget(self.stroke_width)
        self.fill = QCheckBox(self._label("Fill", "填充"), toolbar)
        self.fill.toggled.connect(self._style_changed)
        controls.addWidget(self.fill)
        self.style = QComboBox(toolbar)
        self.style.setAccessibleName(self._label("Annotation style", "标注样式"))
        self.style.currentIndexChanged.connect(self._style_changed)
        controls.addWidget(self.style)
        self.number_group = QComboBox(toolbar)
        self.number_group.setAccessibleName(self._label("Numbering series", "编号分组"))
        controls.addWidget(self.number_group)
        self._fill_number_groups()
        self.number_group.currentIndexChanged.connect(self._change_number_group)
        self.number_spin = QSpinBox(toolbar)
        self.number_spin.setRange(1, 9999)
        self.number_spin.setPrefix(self._label("Next ", "下一号 "))
        self.number_spin.setValue(self._next_numbers[self._number_series])
        self.number_spin.setAccessibleName(self._label("Next step number", "下一步骤序号"))
        self.number_spin.valueChanged.connect(self._change_next_number)
        controls.addWidget(self.number_spin)
        controls.addStretch()
        self.undo_button = self._button(controls, self._label("Undo", "撤销"), self._undo, shortcut="Ctrl+Z")
        self.redo_button = self._button(controls, self._label("Redo", "重做"), self._redo, shortcut="Ctrl+Shift+Z")
        self.undo_button.setEnabled(False)
        self.redo_button.setEnabled(False)
        if not self._embedded:
            actions = QHBoxLayout()
            actions.setSpacing(3)
            layout.addLayout(actions)
            self.dimensions = QLabel(toolbar)
            self.dimensions.setAccessibleName(self._label("Selection dimensions", "选区尺寸"))
            actions.addWidget(self.dimensions)
            actions.addStretch()
            copy_button = self._button(
                actions, self._label("Copy", "复制"), lambda: self._complete("copy"),
                shortcut=" / ".join(filter(None, (self._shortcut_label("copy"), "Enter"))),
            )
            copy_button.setObjectName("copyCapture")
            self._button(actions, self._label("Pin", "贴图"), lambda: self._complete("pin"), shortcut=self._shortcut_label("pin"))
            self._button(actions, self._label("Save", "保存"), lambda: self._complete("save"), shortcut=self._shortcut_label("save"))
            more = self._button(actions, self._label("More ▾", "更多 ▾"), lambda: None)
            more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            more.setMenu(self._output_menu(more))
            self._button(actions, self._label("Cancel", "取消"), self.close, shortcut="Esc")
        self._update_controls()
        return toolbar

    def _output_menu(self, parent):
        menu = QMenu(parent)
        for key, english, chinese in (
            ("quick-save", "Quick save", "快速保存"),
            ("copy-file", "Copy image as file", "复制为文件"),
            ("ocr", "Extract text", "提取文字"),
            ("barcode", "Scan barcode or QR code", "识别条形码或二维码"),
            ("print", "Print", "打印"),
        ):
            menu.addAction(self._label(english, chinese), lambda checked=False, action=key: self._complete(action))
        if _read_option(self.options, "allow_scroll", True):
            menu.addAction(self._label("Scrolling capture", "滚动截图"), lambda: self._complete("scroll"))
        if _read_option(self.options, "allow_share", False):
            menu.addAction(self._label("Share", "系统共享"), lambda: self._complete("share"))
        menu.addAction(self._label("Refresh screenshot", "刷新截图"), self.refresh_requested.emit)
        menu.addAction(self._label("Copy pixel color", "复制像素颜色"), self._copy_color)
        menu.addAction(self._label("Lock selection ratio", "锁定选区比例"), self._choose_ratio)
        menu.addAction(self._label("Set exact dimensions", "设置精确尺寸"), self._choose_size)
        menu.addAction(self._label("Rounded output corners", "导出圆角"), self._choose_radius)
        custom = _read_option(self.options, "custom_outputs", {})
        if custom:
            menu.addSeparator()
            entries = custom.items() if isinstance(custom, Mapping) else ((label, label) for label in custom)
            for identifier, label in entries:
                menu.addAction(str(label), lambda checked=False, name=str(identifier): self._complete(name, custom=True))
        return menu

    def _local_rect(self, geometry):
        return QRect(geometry).translated(-self.snapshot.geometry.topLeft()).intersected(self.rect())

    def selected_geometry(self):
        return self.selection.translated(self.snapshot.geometry.topLeft()) if not self.selection.isEmpty() else QRect()

    def _set_selection(self, rect, *, defer_repaint=False):
        self.selection = QRect(rect).normalized().intersected(self.rect())
        if not defer_repaint:
            self._place_toolbar()
            self.update()

    def _place_toolbar(self):
        if self._embedded:
            return
        if self.selection.isEmpty() or self.color_only or self.drag_mode in {"region", "resize", "move"}:
            self.toolbar.hide()
            return
        self.toolbar.adjustSize()
        screen = QApplication.screenAt(self.selected_geometry().bottomRight())
        floating = screen is not None and (
            self.toolbar.width() > self.width() - 8 or self.toolbar.height() > self.height() - 8
        )
        if floating != self.toolbar.isWindow():
            self.toolbar.setWindowFlags(
                Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                if floating else Qt.WindowType.Widget
            )
            self.toolbar.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, floating)
            self.toolbar.adjustSize()
        if floating:
            area = screen.availableGeometry().translated(-self.snapshot.geometry.topLeft())
        else:
            area = self._local_rect(screen.geometry()) if screen is not None else self.rect()
        if area.isEmpty():
            area = self.rect()
        x = max(area.left() + 4, min(self.selection.right() - self.toolbar.width(), area.right() - self.toolbar.width() - 3))
        below = self.selection.bottom() + 8
        above = self.selection.top() - self.toolbar.height() - 7
        if below + self.toolbar.height() <= area.bottom():
            y = below
        elif above >= area.top():
            y = above
        else:
            y = max(area.top() + 4, area.bottom() - self.toolbar.height() - 4)
        self.toolbar.move(self.mapToGlobal(QPoint(x, y)) if floating else QPoint(x, y))
        size_for = getattr(self.snapshot, "pixel_size", None)
        if callable(size_for):
            pixels = size_for(self.selected_geometry())
        else:
            dpr = self.snapshot.image.devicePixelRatio()
            pixels = QSize(round(self.selection.width() * dpr), round(self.selection.height() * dpr))
        self.dimensions.setText(f"{pixels.width()} × {pixels.height()} px")
        self.toolbar.show()
        self.toolbar.raise_()

    def set_snapshot(self, snapshot, windows=None):
        geometry = self.selected_geometry()
        self.snapshot = snapshot
        if windows is not None:
            self._window_bounds = [(QRect(rect), str(title)) for rect, title in windows]
            self.windows = self._window_bounds
        if not self._embedded:
            self.setGeometry(snapshot.geometry)
        self._base_selection = QRect()
        self._render_key = None
        self._set_selection(self._local_rect(geometry) if not geometry.isEmpty() else QRect())

    def _selection_base(self):
        if self.selection.isEmpty():
            return QImage()
        if self._base_selection != self.selection:
            self._base_selection = QRect(self.selection)
            self._base_image = self.snapshot.crop(self.selected_geometry())
        return self._base_image

    def export_image(self):
        if self.crop_mode:
            return render_annotations(self.snapshot.image, self.document.annotations).copy(self.selection)
        return render_annotations(
            self._selection_base(), self.document.annotations,
            getattr(self, "_corner_radius", _read_option(self.options, "corner_radius", 0)),
            self.selection.size(),
        )

    def _complete(self, action, *, custom=False):
        if self.finished:
            return
        if self.selection.isEmpty():
            hovered = self._hover_rect()
            if not hovered.isEmpty():
                self._set_selection(hovered)
            else:
                screen = QApplication.screenAt(self.mapToGlobal(self.position))
                self._set_selection(self._local_rect(screen.geometry()) if screen is not None else self.rect())
        image = self.export_image()
        if image.isNull():
            return
        geometry = self.selected_geometry()
        if action == "save" and not custom:
            self.completed.emit(action, image, geometry)
            return
        self.finished = True
        self.hide()
        if custom:
            self.custom_output.emit(action, image, geometry)
        else:
            self.completed.emit(action, image, geometry)
        self.close()

    def closeEvent(self, event):
        if not self.finished and not self._embedded:
            self.finished = True
            self.cancelled.emit()
        super().closeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        self.position = self.mapFromGlobal(QCursor.pos())
        self._update_hover()
        self._schedule_probe()
        self._place_toolbar()
        if not self._embedded:
            self.activateWindow()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def hideEvent(self, event):
        self._magnifying = False
        if self.toolbar.isWindow():
            self.toolbar.hide()
        super().hideEvent(event)

    def focusOutEvent(self, event):
        self._set_magnifying(False)
        super().focusOutEvent(event)

    def leaveEvent(self, event):
        self._set_magnifying(False)
        super().leaveEvent(event)

    def _selected_annotation(self):
        if len(self.selected_items) != 1:
            return None
        index = next(iter(self.selected_items))
        if index < len(self.document.annotations):
            return self.document.annotations[index]
        return None

    def _styles(self, tool):
        styles = [("default", "Standard", "标准")]
        if tool == "arrow":
            styles += [
                ("open", "Open head", "空心箭头"), ("double", "Two heads", "双向箭头"),
                ("reverse", "Reverse", "反向箭头"), ("dashed", "Dashed", "虚线"), ("dotted", "Dotted", "点线"),
            ]
        elif tool in {"ellipse", "line", "rectangle", "pen"}:
            styles += [("dashed", "Dashed", "虚线"), ("dotted", "Dotted", "点线")]
            if tool == "rectangle":
                styles.append(("rounded", "Rounded", "圆角"))
        elif tool in EFFECT_TOOLS | {"redact"}:
            styles.append(("ellipse", "Ellipse", "椭圆"))
        elif tool == "marker":
            styles += [("rectangle", "Rectangle", "矩形"), ("ellipse", "Ellipse", "椭圆")]
        elif tool == "text":
            styles.append(("bold", "Bold", "加粗"))
        return styles

    def _update_controls(self):
        selected = self._selected_annotation()
        tool = selected.tool if selected is not None and self.tool == "select" else self.tool
        controls = (self.style, self.stroke_width, self.fill)
        for control in controls:
            control.blockSignals(True)
        previous_style = self.style.currentData()
        self.style.clear()
        styles = self._styles(tool)
        for value, english, chinese in styles:
            self.style.addItem(self._label(english, chinese), value)
        chosen = selected.style if selected is not None else previous_style
        self.style.setCurrentIndex(max(0, self.style.findData(chosen)))
        self.style.setVisible(len(styles) > 1)
        self.fill.setVisible(tool in {"rectangle", "ellipse"})
        self.number_group.setVisible(tool == "number")
        self.number_spin.setVisible(tool == "number")
        self.color_button.setEnabled(tool not in EFFECT_TOOLS and tool != "select")
        self.stroke_width.setEnabled(tool != "select")
        if selected is not None:
            self.color = selected.color
            self.stroke_width.setValue(selected.width)
            self.fill.setChecked(selected.fill)
            self._update_color_button()
        for control in controls:
            control.blockSignals(False)
        self.undo_button.setEnabled(bool(self.document.undo_history))
        self.redo_button.setEnabled(bool(self.document.redo_history))
        self._place_toolbar()

    def _set_tool(self, tool):
        if self.line_points:
            self._finish_line()
        self.tool = tool
        if tool != "select":
            self.selected_items.clear()
        for name, button in self.tool_buttons.items():
            button.setChecked(name == tool)
        self._update_controls()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.update()

    def _update_color_button(self):
        pixmap = QPixmap(16, 16)
        pixmap.fill(QColor(self.color))
        self.color_button.setIcon(QIcon(pixmap))

    def _choose_color(self):
        color = QColorDialog.getColor(
            QColor(self.color), self, self._label("Annotation color", "标注颜色"),
            QColorDialog.ColorDialogOption.ShowAlphaChannel,
        )
        if color.isValid():
            self.color = color.name(QColor.NameFormat.HexArgb)
            self._update_color_button()
            self._style_changed()
        self.setFocus()

    def _style_changed(self, *_):
        selected = self._selected_annotation()
        if self.tool == "select" and selected is not None:
            updated = list(self.document.annotations)
            index = next(iter(self.selected_items))
            updated[index] = replace(
                selected, color=self.color, width=self.stroke_width.value(),
                fill=self.fill.isChecked(), style=self.style.currentData() or "default",
            )
            self._commit(updated)
        self.update()

    def _change_next_number(self, value):
        self._next_numbers[self._number_series] = value

    def _fill_number_groups(self):
        self.number_group.blockSignals(True)
        self.number_group.clear()
        for series in self._next_numbers:
            self.number_group.addItem(series or self._label("Default series", "默认分组"), series)
        self.number_group.addItem(self._label("New series…", "新建分组…"))
        self.number_group.setCurrentIndex(self.number_group.findData(self._number_series))
        self.number_group.blockSignals(False)

    def _change_number_group(self, index):
        series = self.number_group.itemData(index)
        if series is None:
            label, accepted = QInputDialog.getText(
                self, self._label("New numbering series", "新建编号分组"),
                self._label("Series name", "分组名称"),
            )
            series = label.strip()[:48] if accepted else ""
            if series:
                self._next_numbers.setdefault(series, 1)
            else:
                series = self._number_series
            self._number_series = series
            self._fill_number_groups()
        else:
            self._number_series = series
        self.number_spin.setValue(self._next_numbers[self._number_series])
        self._place_toolbar()

    def _choose_ratio(self):
        text, confirmed = QInputDialog.getText(
            self, self._label("Selection ratio", "选区比例"),
            self._label("Enter width:height, or leave blank to unlock.", "输入宽:高，留空解除锁定。"),
            text=f"{self.aspect:g}:1" if self.aspect else "",
        )
        if not confirmed:
            return
        try:
            values = [float(value) for value in text.split(":")] if text.strip() else []
            ratio = self._aspect_value(values if len(values) == 2 else (values[0] if values else 0))
            if 0 <= ratio < 10000:
                self.aspect = ratio
        except ValueError:
            return
        if self.aspect and not self.selection.isEmpty():
            self._set_selection(self._rect_from_points(self.selection.topLeft(), self.selection.bottomRight()))
        self.setFocus()

    def _choose_size(self):
        screen = QApplication.screenAt(self.selection.center() + self.snapshot.geometry.topLeft())
        scale = screen.devicePixelRatio() if screen else self.snapshot.image.devicePixelRatio()
        width, confirmed = QInputDialog.getInt(
            self, self._label("Exact selection size", "精确选区尺寸"),
            self._label("Width in pixels", "像素宽度"),
            max(1, round(self.selection.width() * scale)), 1, max(1, round(self.width() * scale)),
        )
        if not confirmed:
            return
        height, confirmed = QInputDialog.getInt(
            self, self._label("Exact selection size", "精确选区尺寸"),
            self._label("Height in pixels", "像素高度"),
            max(1, round(self.selection.height() * scale)), 1, max(1, round(self.height() * scale)),
        )
        if confirmed:
            self._apply_preset(QSize(max(1, round(width / scale)), max(1, round(height / scale))))
            self.setFocus()

    def _choose_radius(self):
        radius, confirmed = QInputDialog.getInt(
            self, self._label("Rounded corners", "圆角"),
            self._label("Corner radius", "圆角半径"),
            int(getattr(self, "_corner_radius", _read_option(self.options, "corner_radius", 0))), 0, 1000,
        )
        if confirmed:
            self._corner_radius = radius
            self.update()
        self.setFocus()

    def _apply_preset(self, size):
        if not isinstance(size, QSize):
            size = QSize(*size)
        size = size.boundedTo(self.size())
        anchor = self.selection.topLeft() if not self.selection.isEmpty() else self.position
        rect = QRect(anchor, size)
        self._set_selection(self._constrain_move(rect))

    def _commit(self, annotations):
        self.document.commit(annotations)
        self._render_key = None
        self.undo_button.setEnabled(bool(self.document.undo_history))
        self.redo_button.setEnabled(bool(self.document.redo_history))
        self.update()

    def _undo(self):
        self.document.undo()
        self.selected_items.clear()
        self._render_key = None
        self._update_controls()
        self.update()

    def _redo(self):
        self.document.redo()
        self.selected_items.clear()
        self._render_key = None
        self._update_controls()
        self.update()

    def _make_annotation(self, points, *, text=""):
        return Annotation(
            self.tool, tuple(_point_tuple(point) for point in points),
            self.color, self.stroke_width.value(), text, self.fill.isChecked(),
            self.style.currentData() or "default", series=self._number_series if self.tool == "number" else "",
        )

    def _finish_line(self):
        if len(self.line_points) > 1:
            self._commit((*self.document.annotations, self._make_annotation(self.line_points)))
        self.line_points.clear()
        self.update()

    def _edit_text(self, point, item=None):
        title = self._label("Text annotation", "文字标注")
        prompt = self._label("Text", "文字")
        value, confirmed = QInputDialog.getMultiLineText(self, title, prompt, item.text if item else "")
        if confirmed and value.strip():
            if item:
                updated = list(self.document.annotations)
                updated[next(iter(self.selected_items))] = replace(item, text=value)
                self._commit(updated)
            else:
                self._commit((*self.document.annotations, self._make_annotation([point], text=value)))
        self.setFocus()

    def _update_selected(self, **values):
        self._commit(
            replace(item, **values) if index in self.selected_items else item
            for index, item in enumerate(self.document.annotations)
        )

    def _rotate_selected(self, delta):
        rotatable = REGION_TOOLS - EFFECT_TOOLS | {"text", "number"}
        self._commit(
            replace(item, angle=(item.angle + delta) % 360)
            if index in self.selected_items and item.tool in rotatable else item
            for index, item in enumerate(self.document.annotations)
        )

    def _choose_text_decoration(self, kind, item):
        color = QColor(getattr(item, kind) or "#ffffff")
        color = QColorDialog.getColor(color, self, self._label("Text decoration color", "文字装饰颜色"),
                                      QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if not color.isValid():
            return
        color = color.name(QColor.NameFormat.HexArgb)
        if kind == "background":
            self._update_selected(background=color)
        else:
            width, accepted = QInputDialog.getDouble(
                self, self._label("Text outline", "文字描边"), self._label("Outline width", "描边宽度"),
                item.outline_width, 0.25, 20.0, 2,
            )
            if accepted:
                self._update_selected(outline=color, outline_width=width)

    def _choose_text_font(self, item):
        accepted, font = QFontDialog.getFont(item.font(), self, self._label("Text font", "文字字体"))
        if accepted:
            self._update_selected(font_family=font.family())

    def _remove_selected(self):
        self._commit(item for index, item in enumerate(self.document.annotations) if index not in self.selected_items)
        self.selected_items.clear()
        self._update_controls()

    def _edit_menu(self, item):
        menu = self._output_menu(self) if not self._embedded else QMenu(self)
        if menu.actions():
            menu.addSeparator()
        if item is not None and item.tool == "text":
            text_menu = menu.addMenu(self._label("Text appearance", "文字外观"))
            text_menu.addAction(self._label("Edit text", "编辑文字"), lambda: self._edit_text(QPointF(), item))
            text_menu.addAction(self._label("Font", "字体"), lambda: self._choose_text_font(item))
            text_menu.addAction(self._label("Background color", "背景颜色"), lambda: self._choose_text_decoration("background", item))
            text_menu.addAction(self._label("Outline color and width", "描边颜色与宽度"), lambda: self._choose_text_decoration("outline", item))
            text_menu.addAction(self._label("Clear decorations", "清除背景和描边"), lambda: self._update_selected(background="", outline=""))
        if item is not None and item.tool == "number":
            menu.addAction(self._label("Edit step number", "编辑步骤序号"), lambda: self._edit_number(item))
            series_menu = menu.addMenu(self._label("Move to series", "移到编号分组"))
            for series in self._next_numbers:
                series_menu.addAction(
                    series or self._label("Default series", "默认分组"),
                    lambda checked=False, value=series: self._move_number_to_series(item, value),
                )
        if self.selected_items:
            menu.addAction(self._label("Rotate selected left", "向左旋转所选"), lambda: self._rotate_selected(-15))
            menu.addAction(self._label("Rotate selected right", "向右旋转所选"), lambda: self._rotate_selected(15))
            menu.addAction(self._label("Delete selected annotations", "删除所选标注"), self._remove_selected)
        if self.document.annotations:
            menu.addAction(self._label("Clear all annotations", "清空标注"), self._clear_annotations)
        return menu

    def _edit_number(self, item):
        number, accepted = QInputDialog.getInt(
            self, self._label("Step number", "步骤序号"), self._label("Number", "序号"),
            int(item.text) if item.text.isdecimal() else 1, 1, 9999,
        )
        if accepted:
            self._update_selected(text=str(number))
            self._move_number_to_series(replace(item, text=str(number)), item.series)

    def _move_number_to_series(self, item, series):
        self._update_selected(series=series)
        if item.text.isdecimal():
            self._next_numbers[series] = min(9999, max(self._next_numbers.get(series, 1), int(item.text) + 1))
            self.number_spin.setValue(self._next_numbers[self._number_series])

    def _clear_annotations(self):
        self.selected_items.clear()
        self._commit(())
        self._update_controls()

    def _point_in_annotation(self, point):
        for index in range(len(self.document.annotations) - 1, -1, -1):
            annotation = self.document.annotations[index]
            spread = max(5, annotation.width / 2)
            if annotation.visual_bounds().adjusted(-spread, -spread, spread, spread).contains(point):
                return index
        return None

    def _hover_rect(self):
        if 0 <= self.hover_index < len(self.windows):
            return self._local_rect(self.windows[self.hover_index][0])
        return QRect()

    def _update_hover(self):
        self.hover_index = -1
        if not self.selection.isEmpty() or not self.auto_detect:
            return
        position = self.position + self.snapshot.geometry.topLeft()
        for index, (geometry, _) in enumerate(self.windows):
            if geometry.contains(position):
                self.hover_index = index
                break

    def _schedule_probe(self):
        if self._detect_elements is None or not self.auto_detect:
            return
        self._probe_generation += 1
        if not self._probe_active and self.selection.isEmpty() and not self.drag_mode:
            self._probe_timer.start()

    def _probe_elements(self):
        if self.finished or not self.isVisible() or not self.selection.isEmpty() or self.drag_mode:
            return
        generation = self._probe_generation
        position = self.position + self.snapshot.geometry.topLeft()
        self._probe_active = True

        def probe():
            try:
                found = self._detect_elements(position)
            except (OSError, RuntimeError, TypeError, ValueError):
                found = ()
            try:
                self._elements_detected.emit(generation, found)
            except RuntimeError:
                # The overlay may be closed while a platform hit test is in flight.
                pass

        QThreadPool.globalInstance().start(QRunnable.create(probe))

    def _receive_elements(self, generation, found):
        self._probe_active = False
        if self.finished or not self.isVisible() or not self.selection.isEmpty() or self.drag_mode:
            return
        if generation != self._probe_generation:
            self._probe_timer.start()
            return
        bounds = set()
        items = []
        for rect, title in (*found, *self._window_bounds):
            if rect.getRect() not in bounds:
                items.append((rect, title))
                bounds.add(rect.getRect())
        self.windows = items
        self._update_hover()
        self.update()

    def _cycle_windows(self, direction):
        if not self.windows:
            return
        position = self.position + self.snapshot.geometry.topLeft()
        candidates = [index for index, (geometry, _) in enumerate(self.windows) if geometry.contains(position)]
        if not candidates:
            candidates = list(range(len(self.windows)))
        index = candidates.index(self.hover_index) if self.hover_index in candidates else (-1 if direction > 0 else 0)
        self.hover_index = candidates[(index + direction) % len(candidates)]
        self.update()

    def _rect_from_points(self, start, end):
        dx = end.x() - start.x()
        dy = end.y() - start.y()
        if self.aspect > 0:
            width = max(abs(dx), abs(dy) * self.aspect)
            height = width / self.aspect
            end = QPoint(start.x() + round(width) * (-1 if dx < 0 else 1), start.y() + round(height) * (-1 if dy < 0 else 1))
        top_left = QPoint(min(start.x(), end.x()), min(start.y(), end.y()))
        bottom_right = QPoint(max(start.x(), end.x()), max(start.y(), end.y()))
        return QRect(top_left, bottom_right).intersected(self.rect())

    def _constrain_move(self, rect):
        rect = QRect(rect)
        x = max(0, min(rect.x(), self.size().width() - rect.width()))
        y = max(0, min(rect.y(), self.height() - rect.height()))
        rect.moveTopLeft(QPoint(x, y))
        return rect

    def _hit_edges(self, position):
        rect = self.selection
        if not rect.adjusted(-6, -6, 6, 6).contains(position):
            return ()
        edges = []
        if abs(rect.left() - position.x()) <= 6:
            edges.append("left")
        elif abs(rect.right() - position.x()) <= 6:
            edges.append("right")
        if abs(rect.top() - position.y()) <= 6:
            edges.append("top")
        elif abs(rect.bottom() - position.y()) <= 6:
            edges.append("bottom")
        return tuple(edges)

    def _resize_selection(self, position):
        rect = QRect(self.original_selection)
        if "left" in self.resize_edges:
            rect.setLeft(min(position.x(), rect.right() - 1))
        elif "right" in self.resize_edges:
            rect.setRight(max(position.x(), rect.left() + 1))
        if "top" in self.resize_edges:
            rect.setTop(min(position.y(), rect.bottom() - 1))
        elif "bottom" in self.resize_edges:
            rect.setBottom(max(position.y(), rect.top() + 1))
        if self.aspect:
            if len(self.resize_edges) == 1 and self.resize_edges[0] in {"top", "bottom"}:
                rect.setWidth(max(1, round(rect.height() * self.aspect)))
            else:
                height = max(1, round(rect.width() / self.aspect))
                if "top" in self.resize_edges:
                    rect.setTop(rect.bottom() - height + 1)
                else:
                    rect.setHeight(height)
        return rect.intersected(self.rect())

    def _annotation_point(self, position):
        relative = position - self.selection.topLeft()
        return QPointF(
            max(0, min(relative.x(), self.selection.width() - 1)),
            max(0, min(relative.y(), self.selection.height() - 1)),
        )

    def _stroke_endpoint(self, point, mods):
        if not (mods & Qt.KeyboardModifier.ShiftModifier) or not self.points:
            return point
        start = self.points[0]
        delta = point - start
        if self.tool in REGION_TOOLS or (self.tool == "marker" and self.style.currentData() != "default"):
            side = max(abs(delta.x()), abs(delta.y()))
            delta = QPointF(-side if delta.x() < 0 else side, -side if delta.y() < 0 else side)
        elif self.tool in {"arrow", "line"}:
            length = hypot(delta.x(), delta.y())
            direction = round(atan2(delta.y(), delta.x()) / (pi / 4)) * pi / 4
            delta = QPointF(cos(direction) * length, sin(direction) * length)
        else:
            return point
        scale = 1.0
        for coord, change, bound in (
            (start.x(), delta.x(), self.selection.width() - 1),
            (start.y(), delta.y(), self.selection.height() - 1),
        ):
            if change and not 0 <= coord + change <= bound:
                scale = min(scale, ((bound if change > 0 else 0) - coord) / change)
        return start + delta * scale

    def _current_stroke(self):
        if self.line_points:
            return self._make_annotation((*self.line_points, self._annotation_point(self.position)))
        if self.drag_mode != "annotation" or not self.points:
            return None
        return self._make_annotation(self.points)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            if self.line_points:
                self._finish_line()
            elif self.selection.isEmpty() and not self._embedded:
                self.close()
            else:
                position = event.position().toPoint()
                index = self._point_in_annotation(self._annotation_point(position)) if self.selection.contains(position) else None
                if index is not None and index not in self.selected_items:
                    self.selected_items = {index}
                    self._set_tool("select")
                menu = self._edit_menu(self._selected_annotation())
                if menu.actions():
                    menu.exec(event.globalPosition().toPoint())
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.position = event.position().toPoint()
        if self.color_only:
            self._copy_color()
            return
        self.drag_start = self.position
        self.original_selection = QRect(self.selection)
        point = self._annotation_point(self.position)
        if self.crop_mode:
            self.resize_edges = self._hit_edges(self.position) if not self.selection.isEmpty() else ()
            if self.resize_edges:
                self.drag_mode = "resize"
            elif self.selection.contains(self.position) and event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.drag_mode = "move"
            else:
                self.drag_mode = "region"
        elif self.selection.isEmpty():
            self.drag_mode = "region"
            self._update_hover()
        elif self.tool != "select" and self.selection.contains(self.position):
            if self.tool == "text":
                self._edit_text(point)
                return
            if self.tool == "number":
                next_number = self._next_numbers[self._number_series]
                self._commit((*self.document.annotations, self._make_annotation([point], text=str(next_number))))
                self.number_spin.setValue(min(9999, next_number + 1))
                return
            if self.tool == "line":
                self.line_points.append(point)
                self.update()
                return
            self.drag_mode = "annotation"
            self.points = [point]
        elif self.tool == "select":
            index = self._point_in_annotation(point) if self.selection.contains(self.position) else None
            shift = event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            if index is not None:
                if shift and index in self.selected_items:
                    self.selected_items.remove(index)
                elif shift:
                    self.selected_items.add(index)
                elif index not in self.selected_items:
                    self.selected_items = {index}
                self.drag_mode = "items"
                self.original_annotations = self.document.annotations
                self.working_annotations = None
                self._update_controls()
            elif shift and self.selection.contains(self.position):
                self.selected_items.clear()
                self.drag_mode = "marquee"
            elif not self._embedded:
                self.selected_items.clear()
                self.resize_edges = self._hit_edges(self.position)
                if self.resize_edges:
                    self.drag_mode = "resize"
                elif self.selection.contains(self.position):
                    self.drag_mode = "move"
                else:
                    self.drag_mode = "region"
                self._update_controls()
            else:
                self.selected_items.clear()
                self._update_controls()
        if self.drag_mode in {"region", "resize", "move"}:
            self._place_toolbar()
        self.update()

    def mouseMoveEvent(self, event):
        old_position = self.position
        old_selection = self.selection
        old_hover = self._hover_rect() if old_selection.isEmpty() else QRect()
        old_lens = self._visible_magnifier_rect()
        self.position = event.position().toPoint()
        self._magnifying = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        damage = QRegion()
        if self.drag_mode == "region":
            if (self.position - self.drag_start).manhattanLength() > 3 or not self.selection.isEmpty():
                self._set_selection(self._rect_from_points(self.drag_start, self.position), defer_repaint=True)
        elif self.drag_mode == "resize":
            self._set_selection(self._resize_selection(self.position), defer_repaint=True)
        elif self.drag_mode == "move":
            self._set_selection(self._moved_selection(), defer_repaint=True)
        elif self.drag_mode == "items":
            delta = self.position - self.drag_start
            self.working_annotations = tuple(
                annotation.moved(delta) if index in self.selected_items else annotation
                for index, annotation in enumerate(self.original_annotations)
            )
            damage |= QRegion(self.selection)
        elif self.drag_mode == "annotation":
            point = self._annotation_point(self.position)
            if self.tool == "pen" or (self.tool == "marker" and self.style.currentData() == "default"):
                if self.points[-1] != point:
                    self.points.append(point)
            else:
                self.points = [self.points[0], self._stroke_endpoint(point, event.modifiers())]
            damage |= QRegion(self.selection)
        elif self.drag_mode == "marquee" or self.line_points:
            damage |= QRegion(self.selection)
        else:
            self._update_hover()
            if self.position != old_position:
                self._schedule_probe()

        if old_selection != self.selection:
            damage |= self._outline_region(old_selection) | self._outline_region(self.selection)
            if self.crop_mode:
                damage |= QRegion(old_selection).xored(QRegion(self.selection))
            elif self.document.annotations:
                damage |= QRegion(old_selection) | QRegion(self.selection)
        hover = self._hover_rect() if self.selection.isEmpty() else QRect()
        if old_hover != hover:
            damage |= self._outline_region(old_hover) | self._outline_region(hover)
        lens = self._visible_magnifier_rect()
        if old_position != self.position or old_lens != lens:
            damage |= QRegion(old_lens) | QRegion(lens)
        if not damage.isEmpty():
            self.update(damage)

    def _moved_selection(self):
        return self._constrain_move(self.original_selection.translated(self.position - self.drag_start))

    def mouseReleaseEvent(self, event):
        if self.color_only or event.button() != Qt.MouseButton.LeftButton:
            return super().mouseReleaseEvent(event)
        self.position = event.position().toPoint()
        mode, self.drag_mode = self.drag_mode, ""
        if mode == "region":
            if (self.position - self.drag_start).manhattanLength() <= 3:
                hovered = self._hover_rect()
                preset = _read_option(self.options, "preset_size")
                if preset:
                    self._apply_preset(preset)
                elif not hovered.isEmpty():
                    self._set_selection(hovered)
                else:
                    self._set_selection(self.original_selection)
            else:
                self._set_selection(self._rect_from_points(self.drag_start, self.position))
        elif mode == "resize":
            self._set_selection(self._resize_selection(self.position))
        elif mode == "move":
            self._set_selection(self._moved_selection())
        elif mode == "items" and self.working_annotations is not None:
            self._commit(self.working_annotations)
            self.working_annotations = None
        elif mode == "annotation":
            end = self._stroke_endpoint(self._annotation_point(self.position), event.modifiers())
            if self.tool in REGION_TOOLS or self.tool == "arrow" or (
                self.tool == "marker" and self.style.currentData() != "default"
            ):
                self.points = [self.points[0], end] if end != self.points[0] else self.points[:1]
            elif end != self.points[-1]:
                self.points.append(end)
            if self.tool in {"pen", "marker"} or len(self.points) > 1:
                self._commit((*self.document.annotations, self._make_annotation(self.points)))
            self.points = []
        elif mode == "marquee":
            area = QRectF(self._annotation_point(self.drag_start), self._annotation_point(self.position)).normalized()
            self.selected_items = {
                index for index, item in enumerate(self.document.annotations)
                if area.contains(item.bounds().center())
            }
            self._update_controls()
        self._place_toolbar()
        self.update()

    def mouseDoubleClickEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mouseDoubleClickEvent(event)
        if self.tool == "line":
            self._finish_line()
        elif self.tool == "select":
            point = self._annotation_point(event.position().toPoint())
            index = self._point_in_annotation(point) if self.selection.contains(event.position().toPoint()) else None
            item = self.document.annotations[index] if index is not None else None
            if item is not None and item.tool == "text":
                self.selected_items = {index}
                self._update_controls()
                self._edit_text(point, item)
            elif not self._embedded and self.selection.contains(event.position().toPoint()):
                self._complete(_read_option(self.options, "default_action", "copy"))

    def wheelEvent(self, event):
        if self.selection.isEmpty() and self.windows and self.auto_detect and not self.color_only:
            self._cycle_windows(-1 if event.angleDelta().y() > 0 else 1)
            return
        if not self.selected_items or not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            return super().wheelEvent(event)
        multiplier = 1.1 if event.angleDelta().y() > 0 else 1 / 1.1
        self._commit(
            item.scaled(multiplier) if index in self.selected_items else item
            for index, item in enumerate(self.document.annotations)
        )

    def keyPressEvent(self, event):
        key = event.key()
        mods = event.modifiers()
        key_code = Qt.Key
        control = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        if key == key_code.Key_Escape:
            if self.line_points:
                self.line_points.clear()
                self.update()
            elif self._embedded:
                super().keyPressEvent(event)
            else:
                self.close()
            return
        if self.color_only and key in {key_code.Key_Return, key_code.Key_Enter}:
            self._copy_color()
            return
        if event.matches(QKeySequence.StandardKey.Undo):
            self._undo()
            return
        if event.matches(QKeySequence.StandardKey.Redo) or (control and key == key_code.Key_Y):
            self._redo()
            return
        if key in {key_code.Key_Delete, key_code.Key_Backspace} and self.selected_items:
            self._remove_selected()
            return
        if key in {key_code.Key_Return, key_code.Key_Enter}:
            if self.line_points:
                self._finish_line()
            elif self._keyboard_anchor is not None:
                self._end_keyboard_selection()
            elif not self._embedded:
                self._complete(_read_option(self.options, "default_action", "copy"))
            else:
                super().keyPressEvent(event)
            return
        if not self._embedded:
            if key == key_code.Key_Space and not self.color_only:
                if self._keyboard_anchor is not None:
                    self._end_keyboard_selection()
                else:
                    self._keyboard_anchor = QPoint(
                        max(0, min(self.position.x(), self.rect().right())),
                        max(0, min(self.position.y(), self.rect().bottom())),
                    )
                    self.position = QPoint(self._keyboard_anchor)
                    self.drag_mode = "region"
                    self._set_selection(QRect(self.position, QSize(1, 1)))
                return
            if control and key == key_code.Key_A:
                if self.color_only:
                    return
                screen = QApplication.screenAt(self.mapToGlobal(self.position))
                current = self._local_rect(screen.geometry()) if screen else self.rect()
                self._set_selection(self.rect() if self.selection == current else current)
                return
            if key == key_code.Key_Alt:
                self._set_magnifying(True)
                return
            if key == key_code.Key_M and control:
                self.show_magnifier = not self.show_magnifier
                self.update()
                return
            if key in {key_code.Key_Comma, key_code.Key_Period} and self.history:
                direction = -1 if key == key_code.Key_Comma else 1
                self.history_index = max(0, min(len(self.history) - 1, self.history_index + direction))
                self._set_selection(self._local_rect(self.history[self.history_index]))
                return
        directions = {
            key_code.Key_Left: QPoint(-1, 0),
            key_code.Key_Right: QPoint(1, 0),
            key_code.Key_Up: QPoint(0, -1),
            key_code.Key_Down: QPoint(0, 1),
        }
        if key in directions and (not self._embedded or self.crop_mode or self.selected_items):
            self._nudge(directions[key], resize=shift, large=control)
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if not self._embedded and event.key() == Qt.Key.Key_Alt:
            self._set_magnifying(False)
            return
        super().keyReleaseEvent(event)

    def _end_keyboard_selection(self):
        self._keyboard_anchor = None
        self.drag_mode = ""
        self._place_toolbar()
        self.update()

    def focusNextPrevChild(self, forward):
        if not self._embedded and self.selection.isEmpty() and self.windows:
            self._cycle_windows(1 if forward else -1)
            return True
        return super().focusNextPrevChild(forward)

    def _nudge(self, direction, *, resize=False, large=False):
        delta = direction * (10 if large else 1)
        if self.selected_items and not resize:
            self._commit(
                item.moved(delta) if index in self.selected_items else item
                for index, item in enumerate(self.document.annotations)
            )
        elif self.selection.isEmpty() or self._keyboard_anchor is not None:
            self.position += delta
            self.position.setX(max(0, min(self.position.x(), self.rect().right())))
            self.position.setY(max(0, min(self.position.y(), self.rect().bottom())))
            QCursor.setPos(self.mapToGlobal(self.position))
            self._update_hover()
            if self._keyboard_anchor is None:
                self.update()
            else:
                self._set_selection(self._rect_from_points(self._keyboard_anchor, self.position))
        elif (not self._embedded or self.crop_mode) and resize:
            self.original_selection = QRect(self.selection)
            self.resize_edges = ("right",) if direction.x() else ("bottom",)
            self._set_selection(self._resize_selection(self.selection.bottomRight() + delta))
        elif not self._embedded or self.crop_mode:
            self._set_selection(self._constrain_move(self.selection.translated(delta)))

    def _cursor_color(self):
        if not self.rect().contains(self.position):
            return QColor()
        color_at = getattr(self.snapshot, "color_at", None)
        if callable(color_at):
            return color_at(self.position + self.snapshot.geometry.topLeft())
        image = self.snapshot.image
        x = int(self.position.x() * image.width() / self.size().width())
        y = int(self.position.y() * image.height() / self.height())
        return image.pixelColor(x, y)

    def _format_color(self, color):
        mode = str(_read_option(self.options, "color_format", "hex")).lower()
        if mode == "rgb":
            return f"rgb({color.red()}, {color.green()}, {color.blue()})"
        if mode == "hsl":
            return f"hsl({max(0, color.hslHue())}, {round(color.hslSaturationF() * 100)}%, {round(color.lightnessF() * 100)}%)"
        if mode == "hsv":
            return f"hsv({max(0, color.hsvHue())}, {round(color.hsvSaturationF() * 100)}%, {round(color.valueF() * 100)}%)"
        if mode == "cmyk":
            values = ", ".join(f"{round(value * 100)}%" for value in color.getCmykF()[:4])
            return f"cmyk({values})"
        return color.name().upper()

    def _copy_color(self):
        color = self._cursor_color()
        if color.isValid():
            value = self._format_color(color)
            QApplication.clipboard().setText(value)
            self.color_copied.emit(value)

    def _set_magnifying(self, enabled):
        if self._magnifying != enabled:
            old_lens = self._visible_magnifier_rect()
            self._magnifying = enabled
            lens = self._visible_magnifier_rect()
            if old_lens != lens:
                self.update(QRegion(old_lens) | QRegion(lens))

    def _visible_magnifier_rect(self):
        if self._embedded or not self.show_magnifier or not (self.color_only or self._magnifying):
            return QRect()
        position = self.position
        if not self.rect().contains(position):
            return QRect()
        width, height = 162, 155
        screen = QApplication.screenAt(position + self.snapshot.geometry.topLeft())
        bounds = self._local_rect(screen.geometry()) if screen is not None else self.rect()
        if bounds.isEmpty():
            bounds = self.rect()
        x = position.x() + 18 if position.x() + width + 18 <= bounds.right() else position.x() - width - 18
        y = position.y() + 20 if position.y() + height + 20 <= bounds.bottom() else position.y() - height - 20
        return QRect(max(bounds.left(), x), max(bounds.top(), y), width, height)

    def _paint_magnifier(self, painter, lens):
        source = QRect(self.position - QPoint(7, 5), QSize(15, 11)).intersected(self.rect())
        detail = self.snapshot.crop(source.translated(self.snapshot.geometry.topLeft()))
        if detail.isNull():
            return
        panel = lens.adjusted(0, 0, -2, -2)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.setPen(QPen(QColor("#8ed2ff"), 1))
        painter.setBrush(QColor("#172334"))
        painter.drawRoundedRect(panel, 7, 7)
        target = QRect(lens.x() + 6, lens.y() + 6, 148, 111)
        painter.drawImage(QRectF(target), detail)
        x = target.left() + round((self.position.x() - source.left() + 0.5) * target.width() / source.width())
        y = target.top() + round((self.position.y() - source.top() + 0.5) * target.height() / source.height())
        painter.setPen(QPen(QColor("#ffffff"), 1))
        painter.drawLine(x - 11, y, x + 11, y)
        painter.drawLine(x, y - 11, x, y + 11)
        painter.setPen(QPen(QColor("#132131"), 1))
        painter.drawRect(QRect(x - 3, y - 3, 6, 6))
        color = self._cursor_color()
        if color.isValid():
            painter.fillRect(QRect(lens.x() + 9, lens.y() + 128, 12, 12), color)
            painter.setPen(QColor("#f5f7fa"))
            painter.drawText(
                QRect(lens.x() + 27, lens.y() + 120, 122, 28),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                self._format_color(color),
            )
        painter.restore()

    def _paint_selection(self, painter):
        if self.selection.isEmpty():
            return
        annotation_items = self.working_annotations if self.working_annotations is not None else self.document.annotations
        draft = self._current_stroke()
        if (annotation_items or draft is not None) and not self.crop_mode:
            image = self._selection_base()
            if not image.isNull():
                key = (QRect(self.selection), annotation_items)
                if key != self._render_key:
                    self._rendered = render_annotations(image, annotation_items, logical_size=self.selection.size())
                    self._render_key = key
                painter.drawImage(QRectF(self.selection), self._rendered)
                if draft is not None:
                    painter.save()
                    painter.setClipRect(self.selection)
                    painter.translate(self.selection.topLeft())
                    paint_annotation(painter, image, draft, self._rendered, self.selection.size())
                    painter.restore()
        if not self._embedded or self.crop_mode:
            self._paint_outline(painter, self.selection)
            if self.show_guides and not self.drag_mode:
                self._paint_handles(painter)
        if self.selected_items:
            painter.save()
            painter.setClipRect(self.selection)
            painter.translate(self.selection.topLeft())
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(ACCENT, 1, Qt.PenStyle.DashLine))
            for index in self.selected_items:
                if index < len(annotation_items):
                    painter.drawRect(annotation_items[index].visual_bounds().adjusted(-4, -4, 4, 4))
            painter.restore()
        if self.drag_mode == "marquee":
            area = QRect(self.drag_start, self.position).normalized().intersected(self.selection)
            painter.setPen(QPen(ACCENT, 1, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(104, 191, 255, 45))
            painter.drawRect(area)

    def _paint_handles(self, painter):
        rect = self.selection.adjusted(1, 1, -1, -1)
        painter.setBrush(QColor("#e1f4ff"))
        painter.setPen(QPen(QColor("#235579"), 1))
        for x, y in (
            (rect.left(), rect.top()), (rect.right(), rect.top()),
            (rect.left(), rect.bottom()), (rect.right(), rect.bottom()),
            (rect.center().x(), rect.top()), (rect.center().x(), rect.bottom()),
            (rect.left(), rect.center().y()), (rect.right(), rect.center().y()),
        ):
            painter.drawRect(QRect(x - 3, y - 3, 6, 6))

    @staticmethod
    def _outline_region(rect):
        if rect.isEmpty():
            return QRegion()
        # Include both sides of the contrast stroke at fractional display scales.
        return QRegion(rect.adjusted(-3, -3, 3, 3)) - QRegion(rect.adjusted(4, 4, -4, -4))

    @staticmethod
    def _paint_outline(painter, rect):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if rect.width() >= 4 and rect.height() >= 4:
            outline = rect.adjusted(1, 1, -2, -2)
            painter.setPen(QPen(QColor("#101923"), 3))
            painter.drawRect(outline)
        else:
            outline = rect.adjusted(0, 0, -1, -1)
        painter.setPen(QPen(ACCENT, 1))
        painter.drawRect(outline)
        painter.restore()

    def _paint_snapshot(self, painter, damaged):
        frames = getattr(self.snapshot, "frames", ())
        if not frames:
            painter.drawImage(QRectF(self.rect()), self.snapshot.image)
            return
        offset = self.snapshot.geometry.topLeft()
        for frame in frames:
            target = frame.geometry.translated(-offset)
            if damaged.intersects(target):
                painter.drawImage(QRectF(target), frame.image)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(event.rect(), QColor("#152236"))
        self._paint_snapshot(painter, event.region())
        if self.crop_mode and self.document.annotations:
            key = (QRect(self.rect()), self.document.annotations)
            if key != self._render_key:
                self._rendered = render_annotations(self.snapshot.image, self.document.annotations)
                self._render_key = key
            painter.drawImage(QRectF(self.rect()), self._rendered)
        if self.crop_mode:
            dim = QPainterPath()
            dim.addRect(QRectF(self.rect()))
            if not self.selection.isEmpty():
                dim.addRect(QRectF(self.selection))
            painter.fillPath(dim, QColor(9, 16, 29, 126))
        elif not self._embedded and self.selection.isEmpty():
            hovered = self._hover_rect()
            if not hovered.isEmpty():
                self._paint_outline(painter, hovered)
        self._paint_selection(painter)
        lens = self._visible_magnifier_rect()
        if event.region().intersects(lens):
            self._paint_magnifier(painter, lens)
        painter.end()


class _StaticSnapshot:
    def __init__(self, image):
        self.image = QImage(image)
        self.image.setDevicePixelRatio(1.0)
        self.geometry = QRect(QPoint(), self.image.size())

    def crop(self, rect):
        return self.image.copy(rect)


class ImageEditor(QDialog):
    """Edit a pinned or opened image using the same annotation document as capture."""

    applied = Signal(QImage)

    def __init__(self, image, *, parent=None, locale="en", annotations=()):
        super().__init__(parent)
        self.result_image = QImage(image)
        self.result_base = QImage(image)
        self.annotations = tuple(annotations)
        self.snapshot = _StaticSnapshot(image)
        self.canvas = CaptureEditor(
            self.snapshot, selection=self.snapshot.geometry,
            options={"_embedded": True, "_annotations": annotations, "locale": locale},
        )
        self.setWindowTitle(self.canvas._label("Annotate image", "标注图像"))
        self.setAccessibleName(self.windowTitle())
        self.resize(850, 650)
        layout = QVBoxLayout(self)
        layout.addWidget(self.canvas.toolbar, alignment=Qt.AlignmentFlag.AlignHCenter)
        viewport = QScrollArea(self)
        viewport.setAlignment(Qt.AlignmentFlag.AlignCenter)
        viewport.setWidget(self.canvas)
        viewport.setAccessibleName(self.canvas._label("Image canvas", "图像画布"))
        layout.addWidget(viewport, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        self.apply_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.apply_button.setText(self.canvas._label("Apply", "应用"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(self.canvas._label("Cancel", "取消"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def activate_crop(self):
        self.canvas.crop_mode = True
        self.canvas.toolbar.hide()
        self.setWindowTitle(self.canvas._label("Crop image", "裁剪图像"))
        self.apply_button.setText(self.canvas._label("Crop", "裁剪"))
        hint = QLabel(self.canvas._label(
            "Drag to select a crop. Shift-drag moves the selection. Drag an edge to resize.",
            "拖动选择裁剪区域。按住 Shift 拖动可移动选区，拖动边缘可调整尺寸。",
        ), self)
        self.layout().insertWidget(0, hint)
        self.canvas.setAccessibleDescription(hint.text())
        self.canvas.setFocus()

    def accept(self):
        if self.canvas.line_points:
            self.canvas._finish_line()
        image = self.canvas.export_image()
        if image.isNull():
            return
        self.result_image = image
        if self.canvas.crop_mode:
            self.result_base = QImage(image)
            self.annotations = ()
        else:
            self.result_base = QImage(self.snapshot.image)
            self.annotations = self.canvas.document.annotations
        self.applied.emit(image)
        super().accept()
