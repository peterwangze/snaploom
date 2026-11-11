"""Wayland screenshot requests through the desktop's consent portal."""

from __future__ import annotations

import asyncio
from concurrent.futures import Future
from pathlib import Path
from threading import Thread
from uuid import uuid4

from PySide6.QtCore import QEventLoop, QRect, QTimer, QUrl
from PySide6.QtGui import QImage

from .capture import CaptureError, ScreenFrame, _normalized_image


_SERVICE = "org.freedesktop.portal.Desktop"
_PORTAL_PATH = "/org/freedesktop/portal/desktop"
_SCREENSHOT = "org.freedesktop.portal.Screenshot"
_REQUEST = "org.freedesktop.portal.Request"


async def _portal_uri() -> str:
    from dbus_next import Message, MessageType, Variant
    from dbus_next.aio import MessageBus

    bus = await MessageBus().connect()
    request_path = None
    try:
        loop = asyncio.get_running_loop()
        response = loop.create_future()
        early_responses = {}
        token = f"snaploom_{uuid4().hex}"
        sender = bus.unique_name.removeprefix(":").replace(".", "_")
        expected_path = f"{_PORTAL_PATH}/request/{sender}/{token}"

        def on_message(message):
            if (
                message.message_type == MessageType.SIGNAL
                and message.interface == _REQUEST
                and message.member == "Response"
            ):
                if message.path in {request_path, expected_path} and not response.done():
                    response.set_result(message.body)
                elif request_path is None:
                    early_responses[message.path] = message.body

        bus.add_message_handler(on_message)
        reply = await bus.call(
            Message(
                destination="org.freedesktop.DBus",
                path="/org/freedesktop/DBus",
                interface="org.freedesktop.DBus",
                member="AddMatch",
                signature="s",
                body=[f"type='signal',sender='{_SERVICE}',interface='{_REQUEST}',member='Response'"],
            )
        )
        if reply.message_type == MessageType.ERROR:
            raise CaptureError("无法订阅 Wayland 截图门户响应。")
        reply = await asyncio.wait_for(
            bus.call(
                Message(
                    destination=_SERVICE,
                    path=_PORTAL_PATH,
                    interface=_SCREENSHOT,
                    member="Screenshot",
                    signature="sa{sv}",
                    body=["", {"handle_token": Variant("s", token), "interactive": Variant("b", False)}],
                )
            ),
            15,
        )
        if reply.message_type == MessageType.ERROR or not reply.body:
            raise CaptureError(f"Wayland 截图门户不可用（{reply.error_name or '未返回截图请求'}）。")
        request_path = reply.body[0]
        if request_path in early_responses and not response.done():
            response.set_result(early_responses[request_path])
        code, values = await asyncio.wait_for(response, 60)
        if code == 1:
            raise CaptureError("已取消系统截图授权。")
        if code != 0 or "uri" not in values:
            raise CaptureError("系统门户未返回截图。请检查当前桌面环境的截图权限。")
        return values["uri"].value
    except asyncio.TimeoutError as error:
        if request_path:
            try:
                await asyncio.wait_for(
                    bus.call(Message(destination=_SERVICE, path=request_path, interface=_REQUEST, member="Close")),
                    1,
                )
            except asyncio.TimeoutError:
                pass
        raise CaptureError("等待系统截图超时，请重试或检查桌面门户服务。") from error
    finally:
        bus.disconnect()


def _request_capture() -> QImage:
    result: Future[str] = Future()

    def worker():
        try:
            result.set_result(asyncio.run(_portal_uri()))
        except Exception as error:
            result.set_exception(error)

    Thread(target=worker, name="wayland-screenshot", daemon=True).start()
    event_loop = QEventLoop()
    timer = QTimer()
    timer.timeout.connect(lambda: event_loop.quit() if result.done() else None)
    timer.start(25)
    if not result.done():
        event_loop.exec()
    timer.stop()
    if not result.done():
        raise CaptureError("截图请求已停止。")
    try:
        uri = result.result()
    except ImportError as error:
        raise CaptureError("安装包缺少 Wayland 截图依赖 dbus-next。") from error
    except CaptureError:
        raise
    except Exception as error:
        raise CaptureError(f"无法连接 Wayland 截图门户：{error}") from error

    url = QUrl(uri)
    if not url.isLocalFile():
        raise CaptureError("Wayland 截图门户未返回可读取的本地文件。")
    path = Path(url.toLocalFile())
    image = QImage(str(path))
    if image.isNull():
        raise CaptureError("无法读取系统门户返回的截图文件。")
    return _normalized_image(image)


def capture_frames(screens) -> tuple[ScreenFrame, ...]:
    image = _request_capture()
    if len(screens) == 1:
        return (ScreenFrame(screens[0].geometry(), image),)
    geometry = QRect()
    for screen in screens:
        geometry = geometry.united(screen.geometry())
    scale_x = image.width() / geometry.width()
    scale_y = image.height() / geometry.height()
    if abs(scale_x / scale_y - 1) > 0.05:
        raise CaptureError("桌面门户没有返回与当前多屏布局匹配的完整截图。")
    composite = ScreenFrame(geometry, image)
    return tuple(
        ScreenFrame(screen.geometry(), image.copy(composite.native_rect(screen.geometry())))
        for screen in screens
    )
