"""Clipboard inputs, offline recognition and scrolling image stitching."""

from __future__ import annotations

from pathlib import Path
import re

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QImageReader, QPainter, QTextDocument

_ocr_engine = None


def clipboard_images(mime) -> list[QImage]:
    if mime is None:
        return []
    if mime.hasImage():
        image = mime.imageData()
        if not isinstance(image, QImage):
            image = image.toImage() if hasattr(image, "toImage") else QImage()
        if not image.isNull():
            return [image]
    if mime.hasUrls():
        images = [read_image(Path(url.toLocalFile())) for url in mime.urls() if url.isLocalFile()]
        images = [image for image in images if not image.isNull()]
        if images:
            return images
    if mime.hasText() or mime.hasHtml():
        return [text_image(mime.text(), mime.html() if mime.hasHtml() else "")]
    return []


def read_image(path: Path) -> QImage:
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    size = reader.size()
    if size.width() * size.height() > 100_000_000:
        size.scale(QSize(10000, 10000), Qt.AspectRatioMode.KeepAspectRatio)
        reader.setScaledSize(size)
    return reader.read()


def parse_color(text: str) -> QColor:
    if len(text) > 80:
        return QColor()
    rgb = re.fullmatch(r"(?:rgb\s*\()?\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)?",
                       text.strip(), flags=re.IGNORECASE)
    if rgb and all(int(channel) <= 255 for channel in rgb.groups()):
        return QColor(*map(int, rgb.groups()))
    return QColor(text.strip())


def text_image(text: str, html: str = "") -> QImage:
    color = parse_color(text)
    if color.isValid():
        image = QImage(245, 128, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(color)
        painter = QPainter(image)
        painter.setFont(QFont("", 15, QFont.Weight.Bold))
        painter.setPen(QColor("black") if color.lightnessF() > 0.57 else QColor("white"))
        painter.drawText(image.rect(), Qt.AlignmentFlag.AlignCenter, color.name().upper())
        painter.end()
        return image
    document = QTextDocument()
    document.setDocumentMargin(16)
    document.setDefaultFont(QFont("", 13))
    if html and len(html) < 200_000:
        document.setHtml(html)
    else:
        document.setPlainText(text[:40000])
    document.adjustSize()
    document.setTextWidth(min(720, max(115, document.idealWidth())))
    width = min(1024, int(document.size().width()) + 2)
    height = min(4000, max(50, int(document.size().height()) + 2))
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#f8fafc"))
    painter = QPainter(image)
    document.drawContents(painter)
    painter.end()
    return image


def pixels(image: QImage):
    import numpy as np

    rgb = image.convertToFormat(QImage.Format.Format_RGB888)
    array = np.frombuffer(rgb.constBits(), dtype=np.uint8).reshape(rgb.height(), rgb.bytesPerLine())
    return array[:, :rgb.width() * 3].reshape(rgb.height(), rgb.width(), 3).copy()


def recognize(image: QImage, action: str) -> str:
    if action in {"barcode", "barcode-copy"}:
        import zxingcpp

        results = zxingcpp.read_barcodes(pixels(image))
        return "\n".join(dict.fromkeys(result.text for result in results if result.text))
    from rapidocr_onnxruntime import RapidOCR

    global _ocr_engine
    if _ocr_engine is None:
        _ocr_engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
    # RapidOCR accepts OpenCV's BGR byte order for array inputs.
    results, _ = _ocr_engine(pixels(image)[:, :, ::-1].copy())
    return "\n".join(result[1] for result in results or [])


def stitch_images(before: QImage, after: QImage) -> QImage | None:
    """Append a downward-scrolled viewport when the overlap is unambiguous."""
    import cv2
    import numpy as np

    width = before.width()
    height = after.height()
    if width != after.width() or width < 40 or height < 70 or before.height() < height:
        return None
    if before.height() > 30000 or width * before.height() > 100_000_000:
        return None
    previous = before.copy(0, before.height() - height, width, height)
    old = cv2.cvtColor(pixels(previous), cv2.COLOR_RGB2GRAY)
    new = cv2.cvtColor(pixels(after), cv2.COLOR_RGB2GRAY)
    if np.mean(cv2.absdiff(old, new)) < 1.0:
        return None
    inset = max(8, width // 12)
    start = max(8, height // 10)
    length = max(18, height // 6)
    template = new[start:start + length, inset:width - inset]
    if template.std() < 4.0:
        return None
    scores = cv2.matchTemplate(old[:, inset:width - inset], template, cv2.TM_CCOEFF_NORMED)
    _, quality, _, (_, y) = cv2.minMaxLoc(scores)
    displacement = y - start
    if quality < 0.88 or displacement < 4 or displacement > height - length - start:
        return None
    if before.height() + displacement > 30000:
        return None
    combined = QImage(width, before.height() + displacement, QImage.Format.Format_ARGB32_Premultiplied)
    combined.fill(Qt.GlobalColor.transparent)
    painter = QPainter(combined)
    painter.drawImage(QPoint(), before)
    painter.drawImage(QRect(0, before.height(), width, displacement), after,
                      QRect(0, height - displacement, width, displacement))
    painter.end()
    return combined
