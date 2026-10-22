"""Stop the screen from locking or sleeping while a macro plays."""

from __future__ import annotations

import os
import subprocess
import sys


class KeepAwake:
    def __init__(self):
        self._cookie = None
        self._iface = None
        self._proc = None
        self._uninhibit = "UnInhibit"
        self.active = False

    def start(self, reason: str = "Playing a macro"):
        if self.active:
            return
        try:
            if sys.platform == "win32":
                import ctypes
                ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_DISPLAY_REQUIRED = 0x80000000, 0x1, 0x2
                ctypes.windll.kernel32.SetThreadExecutionState(
                    ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
                self.active = True
            elif sys.platform == "darwin":
                self._proc = subprocess.Popen(["caffeinate", "-di", "-w", str(os.getpid())],
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.active = True
            else:
                self.active = self._dbus_inhibit(reason)
        except Exception:
            self.active = False

    def _dbus_inhibit(self, reason: str) -> bool:
        from PySide6.QtDBus import QDBusConnection, QDBusInterface
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            return False
        iface = QDBusInterface("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver",
                               "org.freedesktop.ScreenSaver", bus)
        if iface.isValid():
            reply = iface.call("Inhibit", "Macro Tool", reason)
            args = reply.arguments()
            if args and isinstance(args[0], int):
                self._iface, self._cookie, self._uninhibit = iface, args[0], "UnInhibit"
                return True
        import shutil
        if shutil.which("systemd-inhibit"):
            self._proc = subprocess.Popen(
                ["systemd-inhibit", "--what=idle:sleep", "--who=Macro Tool", f"--why={reason}",
                 "--mode=block", "tail", f"--pid={os.getpid()}", "-f", "/dev/null"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return True
        return False

    def stop(self):
        if not self.active:
            return
        try:
            if sys.platform == "win32":
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
            elif self._proc is not None:
                if sys.platform.startswith("linux"):
                    import signal
                    os.killpg(self._proc.pid, signal.SIGTERM)
                else:
                    self._proc.terminate()
                self._proc.wait(timeout=2)
                self._proc = None
            elif self._iface is not None:
                self._iface.call(self._uninhibit, self._cookie)
                self._iface = self._cookie = None
        except Exception:
            pass
        self.active = False
