"""X11, Windows and macOS through pynput."""

from __future__ import annotations

import sys
import time

from .. import keys
from .base import Backend, BackendError, InputEvent, Listener


def _import():
    try:
        from pynput import keyboard, mouse
    except Exception as e:  # pynput raises various errors when no display is reachable
        raise BackendError(f"Can't reach the display to send input: {e}") from None
    return keyboard, mouse


class PynputBackend(Backend):
    name = "pynput"

    def __init__(self):
        super().__init__()
        keyboard, mouse = _import()
        self._kb_mod = keyboard
        self._mouse_mod = mouse
        self.kb = keyboard.Controller()
        self.mouse = mouse.Controller()
        self.native_pixels = sys.platform != "darwin"
        self.description = {"win32": "Windows", "darwin": "macOS"}.get(sys.platform, "X11")
        self._buttons = self._button_map()
        self._keys = self._key_map()

    def _button_map(self):
        B = self._mouse_mod.Button
        out = {"left": B.left, "right": B.right, "middle": B.middle}
        for name, attrs in (("back", ("x1", "button8")), ("forward", ("x2", "button9"))):
            for attr in attrs:
                if hasattr(B, attr):
                    out[name] = getattr(B, attr)
                    break
        return out

    def _key_map(self):
        K = self._kb_mod.Key
        rename = {
            "super": "cmd", "super_r": "cmd_r",
            "volume_up": "media_volume_up", "volume_down": "media_volume_down",
            "volume_mute": "media_volume_mute",
        }
        out = {}
        for name in keys.NAMED:
            attr = rename.get(name, name)
            if hasattr(K, attr):
                out[name] = getattr(K, attr)
        return out

    def to_pynput(self, key: str):
        if key in self._keys:
            return self._keys[key]
        if len(key) == 1:
            return self._kb_mod.KeyCode.from_char(key)
        raise BackendError(f"{keys.display(key)} isn't available on {self.description}")

    def from_pynput(self, key) -> str | None:
        K = self._kb_mod.Key
        if isinstance(key, K):
            name = key.name
            back = {"cmd": "super", "cmd_l": "super", "cmd_r": "super_r",
                    "ctrl_l": "ctrl", "alt_l": "alt", "shift_l": "shift",
                    "media_volume_up": "volume_up", "media_volume_down": "volume_down",
                    "media_volume_mute": "volume_mute"}
            name = back.get(name, name)
            return name if name in keys.NAMED_SET else None
        char = getattr(key, "char", None)
        if char:
            if len(char) == 1 and ord(char) < 32:
                # Ctrl held: Windows and macOS report control characters.
                if 1 <= ord(char) <= 26:
                    return chr(ord(char) + 96)
                return None
            return char.lower() if char.isalpha() and len(char) == 1 else (char if len(char) == 1 else None)
        vk = getattr(key, "vk", None)
        if vk is not None and sys.platform == "win32":
            if 0x41 <= vk <= 0x5A or 0x30 <= vk <= 0x39:
                return chr(vk).lower()
            if 0x60 <= vk <= 0x69:
                return str(vk - 0x60)
        return None

    def position(self):
        x, y = self.mouse.position
        return int(x), int(y)

    def _move(self, x, y):
        self.mouse.position = (x, y)

    def _button(self, button, down):
        b = self._buttons.get(button)
        if b is None:
            raise BackendError(f"The {button} mouse button isn't available on {self.description}")
        (self.mouse.press if down else self.mouse.release)(b)

    def _scroll(self, dx, dy):
        self.mouse.scroll(dx, dy)

    def _key(self, key, down):
        k = self.to_pynput(key)
        (self.kb.press if down else self.kb.release)(k)

    def _type(self, text):
        for ch in text:
            if ch == "\n":
                self.kb.press(self._kb_mod.Key.enter)
                self.kb.release(self._kb_mod.Key.enter)
            elif ch == "\t":
                self.kb.press(self._kb_mod.Key.tab)
                self.kb.release(self._kb_mod.Key.tab)
            else:
                self.kb.type(ch)
            time.sleep(0.004)

    def listener(self, handler):
        return PynputListener(self, handler)


class PynputListener(Listener):
    def __init__(self, backend: PynputBackend, handler):
        super().__init__(handler)
        self.backend = backend
        self._k = None
        self._m = None

    def start(self):
        keyboard = self.backend._kb_mod
        mouse = self.backend._mouse_mod
        buttons = {v: k for k, v in self.backend._buttons.items()}
        mono = time.monotonic

        def on_press(key, injected=False):
            name = self.backend.from_pynput(key)
            if name:
                self.emit(InputEvent("key", mono(), key=name, down=True))

        def on_release(key, injected=False):
            name = self.backend.from_pynput(key)
            if name:
                self.emit(InputEvent("key", mono(), key=name, down=False))

        def on_move(x, y, injected=False):
            self.emit(InputEvent("move", mono(), x=int(x), y=int(y)))

        def on_click(x, y, button, pressed, injected=False):
            name = buttons.get(button)
            if name:
                self.emit(InputEvent("button", mono(), x=int(x), y=int(y), button=name, down=pressed))

        def on_scroll(x, y, dx, dy, injected=False):
            self.emit(InputEvent("scroll", mono(), x=int(x), y=int(y), dx=int(dx), dy=int(dy)))

        try:
            self._k = keyboard.Listener(on_press=on_press, on_release=on_release)
            self._m = mouse.Listener(on_move=on_move, on_click=on_click, on_scroll=on_scroll)
            self._k.start()
            self._m.start()
            self._k.wait()
            self._m.wait()
        except Exception as e:
            self.stop()
            raise BackendError(f"Can't listen to the keyboard and mouse: {e}") from None
        if sys.platform == "darwin" and not getattr(self._k, "IS_TRUSTED", True):
            self.error = ("macOS is blocking keyboard input. Allow Macro Tool in System Settings > "
                          "Privacy & Security > Accessibility and Input Monitoring, then restart it.")

    def alive(self) -> bool:
        return all(t is not None and t.is_alive() for t in (self._k, self._m))

    def stop(self):
        for listener in (self._k, self._m):
            if listener is not None:
                try:
                    listener.stop()
                except Exception:
                    pass
        self._k = self._m = None
