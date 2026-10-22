"""One global input listener shared by hotkeys and recording."""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, QTimer, Signal

from . import keys
from .backends import Backend, BackendError, InputEvent
from .model import Step
from .recorder import Recorder

DEFAULT_HOTKEYS = {"play": "f9", "stop": "f10", "record": "f8"}
HOTKEY_LABELS = {"play": "Start / stop playing", "stop": "Stop", "record": "Start / stop recording"}


class InputHub(QObject):
    hotkey = Signal(str)

    def __init__(self, backend: Backend, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.matcher = keys.ComboMatcher()
        self.listener = None
        self.error: str | None = None
        self.suspended = 0
        self._recorder: Recorder | None = None
        self._rec_lock = threading.Lock()
        self.hotkeys: dict[str, str] = dict(DEFAULT_HOTKEYS)
        self.restarts = 0
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(5000)
        self._watchdog.timeout.connect(self._check)

    def start(self) -> bool:
        try:
            self.listener = self.backend.listener(self._on_event)
            self.listener.start()
            self.error = self.listener.error
        except BackendError as e:
            self.listener = None
            self.error = str(e)
        if self.listener is not None:
            self._watchdog.start()
        return self.listener is not None

    def _check(self):
        """Restart the listener if its thread died (X server hiccup, device error)."""
        if self.listener is None or self.listener.alive():
            return
        moves = self._recorder is not None and self._recorder.record_moves
        try:
            self.listener.stop()
        except Exception:
            pass
        try:
            self.listener = self.backend.listener(self._on_event)
            self.listener.start()
            if moves:
                self.listener.want_moves(True)
            self.restarts += 1
            self.matcher.reset()
        except BackendError as e:
            self.listener = None
            self.error = str(e)
            self._watchdog.stop()

    def stop(self):
        self._watchdog.stop()
        if self.listener is not None:
            self.listener.stop()
            self.listener = None

    @property
    def active(self) -> bool:
        return self.listener is not None

    def set_hotkeys(self, mapping: dict[str, str]):
        self.hotkeys = dict(mapping)
        combos = {}
        for action, combo in mapping.items():
            if combo:
                combos[keys.parse_combo(combo)] = action
        self.matcher.set_combos(combos)
        self.matcher.reset()

    def _on_event(self, ev: InputEvent):
        if ev.kind == "key":
            if ev.down:
                action = self.matcher.press(ev.key)
                if action and not self.suspended:
                    self.hotkey.emit(action)
            else:
                self.matcher.release(ev.key)
        with self._rec_lock:
            rec = self._recorder
        if rec is not None:
            rec.add(ev)

    # recording
    @property
    def recording(self) -> bool:
        return self._recorder is not None

    def start_recording(self, record_moves: bool):
        combos = [keys.parse_combo(c) for c in self.hotkeys.values() if c]
        rec = Recorder(record_moves=record_moves, ignore_combos=combos)
        with self._rec_lock:
            self._recorder = rec
        if record_moves and self.listener is not None:
            self.listener.want_moves(True)

    def stop_recording(self, from_button: bool) -> tuple[list[Step], bool]:
        with self._rec_lock:
            rec, self._recorder = self._recorder, None
        if self.listener is not None:
            self.listener.want_moves(False)
        if rec is None:
            return [], False
        return rec.finish(time.monotonic(), drop_trailing_click=from_button), rec.full
