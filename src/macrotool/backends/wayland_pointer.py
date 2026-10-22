"""Where the pointer is, on Wayland desktops that will say.

Wayland has no standard way for an app to read the global pointer position.
KDE Plasma reports it to a small KWin script, and Hyprland answers through
hyprctl. Other desktops (GNOME, Sway) get no source; Macro Tool still works
there, but recording can't capture pointer positions.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import time

SERVICE = "io.github.yatovoid.MacroTool"
PATH = "/pointer"
IFACE = "io.github.yatovoid.MacroTool.Pointer"
SCRIPT_NAME = "macrotool-pointer"

KWIN_SCRIPT = f"""
function send() {{
    var p = workspace.cursorPos;
    callDBus("{SERVICE}", "{PATH}", "{IFACE}", "pos", Math.round(p.x) + "," + Math.round(p.y));
}}
workspace.cursorPosChanged.connect(send);
send();
"""


class PointerSource:
    def __init__(self):
        self._lock = threading.Lock()
        self._subs: dict[int, object] = {}
        self._next = 0
        self._pos: tuple[int, int] | None = None
        self.updated = 0.0

    def position(self):
        return self._pos

    def subscribe(self, callback):
        with self._lock:
            token = self._next
            self._next += 1
            self._subs[token] = callback
            first = len(self._subs) == 1
        if first:
            self._activate()

        def unsubscribe():
            with self._lock:
                self._subs.pop(token, None)
                last = not self._subs
            if last:
                self._deactivate()
        return unsubscribe

    def _update(self, x: int, y: int):
        self._pos = (x, y)
        self.updated = time.monotonic()
        with self._lock:
            subs = list(self._subs.values())
        for cb in subs:
            try:
                cb(x, y)
            except Exception:
                pass

    def _activate(self):
        pass

    def _deactivate(self):
        pass

    def close(self):
        with self._lock:
            self._subs.clear()
        self._deactivate()


class HyprlandPointer(PointerSource):
    name = "Hyprland"

    def __init__(self):
        super().__init__()
        self._poll = None
        self._stop = threading.Event()

    @staticmethod
    def available() -> bool:
        return bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")) and shutil.which("hyprctl") is not None

    def _query(self):
        try:
            out = subprocess.run(["hyprctl", "cursorpos"], capture_output=True, text=True, timeout=1).stdout
            x, y = (int(float(v)) for v in out.strip().split(","))
            return x, y
        except (OSError, ValueError, subprocess.SubprocessError):
            return None

    def position(self):
        pos = self._query()
        if pos is not None:
            self._pos = pos
            self.updated = time.monotonic()
        return self._pos

    def _activate(self):
        self._stop.clear()

        def run():
            last = None
            while not self._stop.wait(1 / 60):
                pos = self._query()
                if pos is not None and pos != last:
                    last = pos
                    self._update(*pos)
        self._poll = threading.Thread(target=run, name="hyprland-pointer", daemon=True)
        self._poll.start()

    def _deactivate(self):
        self._stop.set()


class KWinPointer(PointerSource):
    """Needs a running Qt event loop: KWin calls back over D-Bus."""

    name = "KDE Plasma"

    def __init__(self):
        super().__init__()
        from PySide6.QtCore import ClassInfo, QObject, Slot
        from PySide6.QtDBus import QDBusConnection

        source = self

        @ClassInfo({"D-Bus Interface": IFACE})
        class Receiver(QObject):
            @Slot(str)
            def pos(self, text):
                try:
                    x, y = (int(v) for v in text.split(","))
                except ValueError:
                    return
                source._update(x, y)

        self._receiver = Receiver()
        self._bus = QDBusConnection.sessionBus()
        if not self._bus.isConnected():
            raise RuntimeError("no session bus")
        if not self._bus.registerService(SERVICE):
            raise RuntimeError("another Macro Tool owns the D-Bus name")
        self._bus.registerObject(PATH, self._receiver, QDBusConnection.ExportAllSlots)
        self._script_file = None
        self._active = False
        self._pos = self._query_once()

    @staticmethod
    def available() -> bool:
        if "KDE" not in os.environ.get("XDG_CURRENT_DESKTOP", "").upper():
            return False
        try:
            from PySide6.QtDBus import QDBusConnection
        except ImportError:
            return False
        bus = QDBusConnection.sessionBus()
        return bus.isConnected() and bus.interface().isServiceRegistered("org.kde.KWin").value()

    def _scripting(self):
        from PySide6.QtDBus import QDBusInterface
        return QDBusInterface("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", self._bus)

    def _load(self, source: str, name: str) -> bool:
        from PySide6.QtDBus import QDBusInterface
        scripting = self._scripting()
        scripting.call("unloadScript", name)
        fd, path = tempfile.mkstemp(prefix="macrotool-", suffix=".js")
        with os.fdopen(fd, "w") as f:
            f.write(source)
        reply = scripting.call("loadScript", path, name)
        args = reply.arguments()
        if not args or not isinstance(args[0], int) or args[0] < 0:
            os.unlink(path)
            return False
        script = QDBusInterface("org.kde.KWin", f"/Scripting/Script{args[0]}", "org.kde.kwin.Script", self._bus)
        script.call("run")
        self._script_file = path
        return True

    def _unload(self, name: str):
        self._scripting().call("unloadScript", name)
        if self._script_file:
            try:
                os.unlink(self._script_file)
            except OSError:
                pass
            self._script_file = None

    def _query_once(self):
        from PySide6.QtCore import QCoreApplication, QEventLoop
        app = QCoreApplication.instance()
        if app is None or not self._load(KWIN_SCRIPT.replace("workspace.cursorPosChanged.connect(send);", ""),
                                         SCRIPT_NAME + "-once"):
            return None
        deadline = time.monotonic() + 0.5
        while self._pos is None and time.monotonic() < deadline:
            app.processEvents(QEventLoop.AllEvents, 20)
        self._unload(SCRIPT_NAME + "-once")
        return self._pos

    def _activate(self):
        if not self._active:
            self._active = self._load(KWIN_SCRIPT, SCRIPT_NAME)

    def _deactivate(self):
        if self._active:
            self._unload(SCRIPT_NAME)
            self._active = False

    def close(self):
        super().close()
        self._bus.unregisterObject(PATH)
        self._bus.unregisterService(SERVICE)


def detect() -> PointerSource | None:
    try:
        if KWinPointer.available():
            return KWinPointer()
    except Exception:
        pass
    if HyprlandPointer.available():
        return HyprlandPointer()
    return None
