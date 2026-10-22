from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable


class BackendError(RuntimeError):
    """Input can't be sent or read. The message says how to fix it."""


@dataclass
class InputEvent:
    kind: str  # move, button, scroll, key
    t: float  # time.monotonic()
    x: int | None = None
    y: int | None = None
    button: str = ""
    down: bool = False
    dx: int = 0
    dy: int = 0
    key: str = ""


Handler = Callable[[InputEvent], None]


class Listener:
    """Reads global input on a background thread and calls a handler."""

    def __init__(self, handler: Handler):
        self.handler = handler
        self.error: str | None = None

    def start(self):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

    def want_moves(self, on: bool):
        """Pointer motion is only needed while recording."""

    def emit(self, event: InputEvent):
        try:
            self.handler(event)
        except Exception:  # a handler bug must not kill the listener thread
            import traceback
            traceback.print_exc()


class Backend:
    name = "base"
    description = ""
    # True if position() reports where the real pointer is.
    can_read_pointer = True
    # True if text with any character can be typed.
    full_unicode = True
    # Coordinates are native pixels (X11, Windows) rather than logical units.
    native_pixels = True

    def __init__(self):
        self._lock = threading.RLock()
        self._held_buttons: set[str] = set()
        self._held_keys: list[str] = []

    # Subclasses implement the _raw methods; the public ones track what is
    # held so release_all() can always leave the system clean.
    def position(self) -> tuple[int, int] | None:
        raise NotImplementedError

    def _move(self, x: int, y: int):
        raise NotImplementedError

    def _button(self, button: str, down: bool):
        raise NotImplementedError

    def _scroll(self, dx: int, dy: int):
        raise NotImplementedError

    def _key(self, key: str, down: bool):
        raise NotImplementedError

    def _type(self, text: str):
        raise NotImplementedError

    def listener(self, handler: Handler) -> Listener:
        raise NotImplementedError

    def move(self, x: int, y: int):
        with self._lock:
            self._move(int(x), int(y))

    def button(self, button: str, down: bool):
        with self._lock:
            self._button(button, down)
            if down:
                self._held_buttons.add(button)
            else:
                self._held_buttons.discard(button)

    def click(self, button: str, count: int = 1, hold_s: float = 0.0):
        for i in range(count):
            self.button(button, True)
            if hold_s:
                time.sleep(hold_s)
            self.button(button, False)
            if i + 1 < count:
                time.sleep(0.04)

    def scroll(self, dx: int, dy: int):
        with self._lock:
            self._scroll(dx, dy)

    def key(self, key: str, down: bool):
        with self._lock:
            self._key(key, down)
            if down:
                if key not in self._held_keys:
                    self._held_keys.append(key)
            elif key in self._held_keys:
                self._held_keys.remove(key)

    def combo(self, keys: tuple[str, ...]):
        for k in keys:
            self.key(k, True)
        for k in reversed(keys):
            self.key(k, False)

    def type_text(self, text: str):
        with self._lock:
            self._type(text)

    @property
    def holding(self) -> bool:
        return bool(self._held_buttons or self._held_keys)

    def release_all(self):
        with self._lock:
            for key in reversed(list(self._held_keys)):
                try:
                    self._key(key, False)
                except Exception:
                    pass
            self._held_keys.clear()
            for button in list(self._held_buttons):
                try:
                    self._button(button, False)
                except Exception:
                    pass
            self._held_buttons.clear()

    def close(self):
        self.release_all()
