"""Explicitly selected OCR.space and Tencent Cloud OCR requests."""

from __future__ import annotations

from base64 import b64encode
from datetime import datetime, timezone
import hashlib
import hmac
from http.client import HTTPException
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from PySide6.QtCore import QBuffer, QIODevice, QPoint, Qt
from PySide6.QtGui import QImage, QPainter


class CloudOCRError(RuntimeError):
    """The requested provider could not return a usable OCR response."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OCR_SPACE_ENDPOINT = "https://api.ocr.space/parse/image"
_TENCENT_HOST = "ocr.tencentcloudapi.com"
_TENCENT_CONTENT_TYPE = "application/json; charset=utf-8"
_RESPONSE_LIMIT = 8 * 1024 * 1024


def _env(name: str, *, required: bool = True) -> str:
    value = os.environ.get(name, "").strip()
    if not value and required:
        raise CloudOCRError(f"请配置环境变量 {name}，再重试云端文字识别。")
    if any(not 33 <= ord(char) <= 126 for char in value):
        raise CloudOCRError(f"环境变量 {name} 含无效字符。")
    return value


def _encode_image(image: QImage, limit: int) -> tuple[str, bytes]:
    if image.isNull():
        raise CloudOCRError("没有可识别的图像。")

    # Copy only rendered pixels. Metadata and fully transparent pixels do not leave the app.
    opaque = QImage(image.size(), QImage.Format.Format_RGB32)
    if opaque.isNull():
        raise CloudOCRError("截图太大，无法准备云端文字识别。")
    opaque.fill(Qt.GlobalColor.white)
    painter = QPainter(opaque)
    source = QImage(image)
    source.setDevicePixelRatio(1)
    painter.drawImage(QPoint(0, 0), source)
    painter.end()

    for image_format, quality in (("PNG", -1), ("JPEG", 92), ("JPEG", 76), ("JPEG", 55)):
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        saved = opaque.save(buffer, image_format, quality)
        if saved and buffer.size() <= limit:
            return image_format, bytes(buffer.data())
    raise CloudOCRError("图像超过云端识别服务的大小限制，请缩小截图范围。")


def _send_json(request: Request, provider: str) -> dict:
    opener = build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=20) as response:
            data = response.read(_RESPONSE_LIMIT + 1)
    except HTTPError as error:
        error.close()
        raise CloudOCRError(f"{provider} 返回 HTTP {error.code}，请检查凭证及账户额度。") from None
    except (URLError, HTTPException, OSError):
        raise CloudOCRError(f"无法连接 {provider}，请检查网络后重试。") from None
    if len(data) > _RESPONSE_LIMIT:
        raise CloudOCRError(f"{provider} 返回结果过大。")
    try:
        document = json.loads(data)
    except (UnicodeError, ValueError):
        raise CloudOCRError(f"{provider} 返回了无法读取的识别结果。") from None
    if not isinstance(document, dict):
        raise CloudOCRError(f"{provider} 返回了无法读取的识别结果。")
    return document


def _read_lines(items, key: str, provider: str) -> str:
    if not isinstance(items, list):
        raise CloudOCRError(f"{provider} 的识别结果缺少文本字段。")
    lines = []
    for entry in items:
        if not isinstance(entry, dict) or not isinstance(entry.get(key), str):
            raise CloudOCRError(f"{provider} 的识别结果缺少文本字段。")
        if value := entry[key].strip():
            lines.append(value)
    return "\n".join(lines)


def _ocr_space(image: QImage) -> str:
    key = _env("SNAPLOOM_OCR_SPACE_KEY")
    image_format, content = _encode_image(image, 1_000_000)
    extension = "png" if image_format == "PNG" else "jpg"
    mime_type = "image/png" if image_format == "PNG" else "image/jpeg"
    boundary = f"Snaploom{uuid4().hex}"
    fields = {"OCREngine": "2", "language": "auto", "isOverlayRequired": "false"}
    body = bytearray()
    for name, value in fields.items():
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
    body += (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="capture.{extension}"\r\n'
        f"Content-Type: {mime_type}\r\n\r\n"
    ).encode()
    body += content
    body += f"\r\n--{boundary}--\r\n".encode()
    request = Request(
        _OCR_SPACE_ENDPOINT,
        data=bytes(body),
        headers={"apikey": key, "Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    result = _send_json(request, "OCR.space")
    if result.get("IsErroredOnProcessing") or result.get("OCRExitCode") not in (1, 2, "1", "2"):
        raise CloudOCRError("OCR.space 无法识别图像。请检查凭证、额度或缩小截图范围。")
    return _read_lines(result.get("ParsedResults"), "ParsedText", "OCR.space")


def _tencent_authorization(secret_id: str, secret_key: str, timestamp: int, body: bytes) -> str:
    date = datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d")
    algorithm = "TC3-HMAC-SHA256"
    scope = f"{date}/ocr/tc3_request"
    signed = "content-type;host"
    canonical_headers = f"content-type:{_TENCENT_CONTENT_TYPE}\nhost:{_TENCENT_HOST}\n"
    canonical_request = f"POST\n/\n\n{canonical_headers}\n{signed}\n{hashlib.sha256(body).hexdigest()}"
    to_sign = f"{algorithm}\n{timestamp}\n{scope}\n{hashlib.sha256(canonical_request.encode()).hexdigest()}"
    date_key = hmac.digest(f"TC3{secret_key}".encode(), date.encode(), "sha256")
    service_key = hmac.digest(date_key, b"ocr", "sha256")
    signing_key = hmac.digest(service_key, b"tc3_request", "sha256")
    signature = hmac.digest(signing_key, to_sign.encode(), "sha256").hex()
    return f"{algorithm} Credential={secret_id}/{scope}, SignedHeaders={signed}, Signature={signature}"


def _tencent(image: QImage) -> str:
    secret_id = _env("TENCENTCLOUD_SECRET_ID")
    secret_key = _env("TENCENTCLOUD_SECRET_KEY")
    region = _env("TENCENTCLOUD_REGION", required=False)
    _, content = _encode_image(image, 7_499_997)
    body = json.dumps({"ImageBase64": b64encode(content).decode("ascii"), "LanguageType": "auto"}).encode()
    timestamp = int(datetime.now(timezone.utc).timestamp())
    headers = {
        "Authorization": _tencent_authorization(secret_id, secret_key, timestamp, body),
        "Host": _TENCENT_HOST,
        "Content-Type": _TENCENT_CONTENT_TYPE,
        "X-TC-Action": "GeneralBasicOCR",
        "X-TC-Version": "2018-11-19",
        "X-TC-Timestamp": str(timestamp),
    }
    if region:
        headers["X-TC-Region"] = region
    request = Request(f"https://{_TENCENT_HOST}/", data=body, headers=headers, method="POST")
    result = _send_json(request, "腾讯云 OCR").get("Response")
    if not isinstance(result, dict):
        raise CloudOCRError("腾讯云 OCR 返回了无法读取的识别结果。")
    if "Error" in result:
        error = result["Error"]
        code = error.get("Code", "") if isinstance(error, dict) else ""
        if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9.]{1,80}", code):
            raise CloudOCRError(f"腾讯云 OCR 请求失败（{code}）。")
        raise CloudOCRError("腾讯云 OCR 请求失败，请检查凭证与账户配置。")
    return _read_lines(result.get("TextDetections"), "DetectedText", "腾讯云 OCR")


def recognize_cloud(image: QImage, provider: str) -> str:
    """Send rendered pixels to the explicitly selected OCR provider."""
    if provider == "ocr-space":
        return _ocr_space(image)
    if provider == "tencent":
        return _tencent(image)
    raise CloudOCRError("请选择受支持的云端文字识别服务。")
