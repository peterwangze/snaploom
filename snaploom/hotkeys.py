"""System hotkeys with explicit registration status."""

from __future__ import annotations

import asyncio
import ctypes
import os
import sys
import threading
import uuid

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal
from PySide6.QtWidgets import QApplication


LABELS = {
    "capture": "Capture a region", "paste": "Paste or restore a pin",
    "toggle_pins": "Show or hide pins", "repeat": "Repeat last capture",
    "click_through": "Toggle pin click-through", "active": "Capture the active window",
}
MODIFIERS = {"alt": 1, "ctrl": 2, "shift": 4, "super": 8}
ALIASES = {"control": "ctrl", "command": "super", "cmd": "super", "win": "super", "meta": "super",
           "escape": "esc", "return": "enter", "pageup": "page_up", "pagedown": "page_down"}
SPECIAL = {"enter": 0x0D, "space": 0x20, "esc": 0x1B, "tab": 0x09, "backspace": 0x08,
           "delete": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23,
           "page_up": 0x21, "page_down": 0x22, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
           "print_screen": 0x2C}


def parse(shortcut):
    pieces = [part.strip().lower() for part in shortcut.split("+")]
    pieces = [ALIASES.get(part, part) for part in pieces]
    key = pieces[-1]
    modifiers = set(pieces[:-1])
    if not modifiers <= MODIFIERS.keys() or len(modifiers) != len(pieces) - 1:
        raise ValueError(shortcut)
    if key in SPECIAL:
        code = SPECIAL[key]
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 20:
        code = 0x6F + int(key[1:])
    elif len(key) == 1 and key.isascii() and key.isalnum() and modifiers:
        code = ord(key.upper())
    else:
        raise ValueError(shortcut)
    return modifiers, key, code


class WindowsKeys(QAbstractNativeEventFilter):
    def __init__(self, emit):
        super().__init__()
        self.emit = emit
        self.bound = {}
        QApplication.instance().installNativeEventFilter(self)

    def register(self, names):
        user32 = ctypes.windll.user32
        unavailable = []
        for slot, (action, (modifiers, key, code)) in enumerate(names.items(), 1):
            if user32.RegisterHotKey(None, slot, 0x4000 | sum(MODIFIERS[item] for item in modifiers), code):
                self.bound[slot] = action
            else:
                unavailable.append(action)
        return unavailable

    def nativeEventFilter(self, event_type, message):
        from ctypes import wintypes

        if bytes(event_type).startswith(b"windows_"):
            event = wintypes.MSG.from_address(int(message))
            if event.message == 0x0312 and event.wParam in self.bound:
                self.emit(self.bound[event.wParam])
                return True, 0
        return False, 0

    def close(self):
        for slot in self.bound:
            ctypes.windll.user32.UnregisterHotKey(None, slot)
        QApplication.instance().removeNativeEventFilter(self)


class PortalKeys:
    def __init__(self, emit, status):
        self.emit = emit
        self.status = status
        self.loop = None
        self.task = None
        self.stopping = threading.Event()

    def start(self, names):
        threading.Thread(target=self._run, args=(names,), daemon=True).start()

    def _run(self, names):
        try:
            asyncio.run(self._listen(names))
        except (Exception, asyncio.CancelledError) as error:
            if not self.stopping.is_set():
                self.status(f"Wayland shortcuts unavailable: {error}")

    async def _listen(self, names):
        from dbus_next import Message, MessageType, Variant
        from dbus_next.aio import MessageBus

        self.loop = asyncio.get_running_loop()
        self.task = asyncio.current_task()
        if self.stopping.is_set():
            return
        bus = await MessageBus().connect()
        iface = "org.freedesktop.portal.GlobalShortcuts"
        request_iface = "org.freedesktop.portal.Request"
        destination = "org.freedesktop.portal.Desktop"
        desktop = "/org/freedesktop/portal/desktop"
        sender = bus.unique_name.lstrip(":").replace(".", "_")
        requests = {}
        session = None

        def receive(message):
            if message.message_type != MessageType.SIGNAL:
                return
            if message.interface == request_iface and message.member == "Response":
                future = requests.get(message.path)
                if future and not future.done():
                    future.set_result(message.body)
            elif message.interface == iface and message.member == "Activated":
                if message.body[0] == session and message.body[1] in names:
                    self.emit(message.body[1])

        bus.add_message_handler(receive)

        async def call(message):
            reply = await bus.call(message)
            if reply.message_type == MessageType.ERROR:
                raise RuntimeError(reply.body[0] if reply.body else reply.error_name)
            return reply.body

        async def request(method, signature, body, options):
            token = "snaploom_" + uuid.uuid4().hex
            path = f"{desktop}/request/{sender}/{token}"
            future = self.loop.create_future()
            requests[path] = future
            options["handle_token"] = Variant("s", token)
            actual = await call(Message(destination=destination, path=desktop, interface=iface,
                                        member=method, signature=signature, body=[*body, options]))
            requests[actual[0]] = future
            code, result = await asyncio.wait_for(future, 90)
            if code:
                raise RuntimeError("Shortcut approval was declined by the desktop.")
            requests.clear()
            return result

        try:
            for interface in (request_iface, iface):
                await call(Message(destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
                                   interface="org.freedesktop.DBus", member="AddMatch", signature="s",
                                   body=[f"type='signal',sender='{destination}',interface='{interface}'"]))
            result = await request("CreateSession", "a{sv}", [], {
                "session_handle_token": Variant("s", "snaploom_" + uuid.uuid4().hex)
            })
            session = result["session_handle"].value
            shortcuts = []
            for name, (modifiers, key, _) in names.items():
                preferred = "+".join([*(modifier.upper() for modifier in sorted(modifiers)),
                                      key.upper() if key.startswith("f") else key])
                shortcuts.append([name, {"description": Variant("s", LABELS[name]),
                                         "preferred_trigger": Variant("s", preferred)}])
            result = await request("BindShortcuts", "oa(sa{sv})sa{sv}", [session, shortcuts, ""], {})
            if not result.get("shortcuts") or not result["shortcuts"].value:
                raise RuntimeError("No shortcuts were bound.")
            self.status("Wayland shortcuts registered in your desktop settings.")
            await asyncio.Future()
        finally:
            bus.disconnect()

    def close(self):
        self.stopping.set()
        if self.loop and self.loop.is_running() and self.task:
            self.loop.call_soon_threadsafe(self.task.cancel)


class Hotkeys(QObject):
    triggered = Signal(str)
    status = Signal(str)

    def __init__(self):
        super().__init__()
        self.backend = None
        self.unavailable = []

    def configure(self, hotkeys):
        self.close()
        names = {}
        taken = set()
        unavailable = []
        for action, shortcut in hotkeys.items():
            if not shortcut or action not in LABELS:
                continue
            try:
                value = parse(shortcut)
                identity = frozenset(value[0]), value[1]
                if identity in taken:
                    raise ValueError(shortcut)
                taken.add(identity)
                names[action] = value
            except ValueError:
                unavailable.append(action)
        if names and sys.platform == "win32":
            self.backend = WindowsKeys(self.triggered.emit)
            unavailable.extend(self.backend.register(names))
        elif names and sys.platform.startswith("linux") and (
            os.getenv("XDG_SESSION_TYPE") == "wayland" or os.getenv("WAYLAND_DISPLAY")
        ):
            self.backend = PortalKeys(self.triggered.emit, self.status.emit)
            self.backend.start(names)
        elif names:
            try:
                from pynput import keyboard

                bindings = {}
                for action, (modifiers, key, _) in names.items():
                    prefixes = ["<cmd>" if item == "super" else f"<{item}>" for item in sorted(modifiers)]
                    binding = "+".join([*prefixes, f"<{key}>" if len(key) > 1 else key])
                    bindings[binding] = lambda target=action: self.triggered.emit(target)
                self.backend = keyboard.GlobalHotKeys(bindings)
                if not self.backend.IS_TRUSTED:
                    self.status.emit("Grant Snaploom permission in Input Monitoring or use the tray actions.")
                self.backend.start()
            except (ImportError, AttributeError, OSError, ValueError) as error:
                unavailable.extend(names)
                self.status.emit(str(error))
        self.unavailable = unavailable
        if unavailable:
            self.status.emit("Hotkeys unavailable: " + ", ".join(LABELS[action] for action in unavailable))
        return unavailable

    def close(self):
        if self.backend:
            if hasattr(self.backend, "close"):
                self.backend.close()
            else:
                self.backend.stop()
            self.backend = None
