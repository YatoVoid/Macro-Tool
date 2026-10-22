"""Keep sleeps short and on time while a macro plays.

Windows wakes sleeping threads on a 15.6 ms tick unless an app asks for
1 ms, which would turn a 1 ms auto clicker into 64 clicks a second. macOS
App Nap stretches the timers of apps in the background, which is exactly
where an auto clicker runs.
"""

from __future__ import annotations

import sys


class PreciseTimers:
    def __init__(self):
        self._token = None
        self._windows = False

    def __enter__(self):
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.winmm.timeBeginPeriod(1)
                self._windows = True
            except Exception:
                pass
        elif sys.platform == "darwin":
            try:
                from Foundation import NSProcessInfo
                user_initiated, latency_critical = 0x00FFFFFF, 0xFF00000000
                self._token = NSProcessInfo.processInfo().beginActivityWithOptions_reason_(
                    user_initiated | latency_critical, "Playing a macro")
            except Exception:
                self._token = None
        return self

    def __exit__(self, *exc):
        if self._windows:
            try:
                import ctypes
                ctypes.windll.winmm.timeEndPeriod(1)
            except Exception:
                pass
            self._windows = False
        if self._token is not None:
            try:
                from Foundation import NSProcessInfo
                NSProcessInfo.processInfo().endActivity_(self._token)
            except Exception:
                pass
            self._token = None
        return False
