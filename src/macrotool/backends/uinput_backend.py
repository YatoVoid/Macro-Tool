"""Wayland (and any Linux console) through the kernel's uinput and evdev.

Wayland doesn't let apps send input to other apps or read global input, so
Macro Tool creates its own virtual keyboard and pointer devices. The
compositor treats them like real hardware, which works on every desktop.
Reading input for recording and hotkeys comes straight from /dev/input.

Both need the user in the "input" group (or a udev rule); see README.
"""

from __future__ import annotations

import os
import selectors
import threading
import time

from .. import keys
from .base import Backend, BackendError, InputEvent, Listener

DEVICE_PREFIX = "Macro Tool virtual"
SETUP_HELP = (
    "Macro Tool needs access to /dev/uinput and /dev/input to work on Wayland.\n"
    "Run this once, then log out and back in:\n\n"
    "    sudo usermod -aG input $USER\n\n"
    "Some distributions also need the uinput module loaded:\n\n"
    "    echo uinput | sudo tee /etc/modules-load.d/uinput.conf\n"
    "    sudo modprobe uinput\n\n"
    "The packages for Arch, Debian and Fedora install a udev rule that does this for you."
)


def _evdev():
    try:
        import evdev
        from evdev import ecodes
    except ImportError:
        raise BackendError("The python evdev module is missing. Install it with: pip install evdev") from None
    return evdev, ecodes


class UinputBackend(Backend):
    name = "uinput"
    description = "Wayland"
    native_pixels = False
    full_unicode = False

    def __init__(self, pointer_source=None):
        super().__init__()
        self.evdev, self.ec = _evdev()
        from . import evdev_keys
        self.k = evdev_keys
        if not os.access("/dev/uinput", os.R_OK | os.W_OK):
            raise BackendError(SETUP_HELP)
        self.pointer_source = pointer_source
        self.can_read_pointer = pointer_source is not None
        self._bounds = (0, 0, 1920, 1080)
        self._last = None
        self._last_t = 0.0
        self._kbd = None
        self._ptr = None
        self._ready_at = 0.0
        try:
            self._kbd = self.evdev.UInput(
                {self.ec.EV_KEY: evdev_keys.ALL_KEY_CODES},
                name=f"{DEVICE_PREFIX} keyboard", bustype=self.ec.BUS_USB,
                vendor=0x1209, product=0x4d54)
        except (OSError, self.evdev.UInputError) as e:
            raise BackendError(f"Can't create a virtual keyboard: {e}\n\n{SETUP_HELP}") from None
        self._make_pointer()

    def set_bounds(self, x: int, y: int, w: int, h: int):
        """The bounding box of all screens, in the compositor's logical pixels."""
        bounds = (int(x), int(y), max(1, int(w)), max(1, int(h)))
        if bounds != self._bounds:
            with self._lock:
                self._bounds = bounds
                self._make_pointer()

    def _make_pointer(self):
        ec = self.ec
        _, _, w, h = self._bounds
        if self._ptr is not None:
            try:
                self._ptr.close()
            except Exception:
                pass
        caps = {
            ec.EV_KEY: list(self.k.BUTTONS.values()),
            ec.EV_REL: [ec.REL_WHEEL, ec.REL_HWHEEL],
            # libinput maps value v to v * size / (max + 1), so max = size - 1
            # lands on exact pixels.
            ec.EV_ABS: [
                (ec.ABS_X, self.evdev.AbsInfo(0, 0, w - 1, 0, 0, 1)),
                (ec.ABS_Y, self.evdev.AbsInfo(0, 0, h - 1, 0, 0, 1)),
            ],
        }
        try:
            self._ptr = self.evdev.UInput(caps, name=f"{DEVICE_PREFIX} pointer",
                                          bustype=ec.BUS_USB, vendor=0x1209, product=0x4d55)
        except (OSError, self.evdev.UInputError) as e:
            raise BackendError(f"Can't create a virtual pointer: {e}\n\n{SETUP_HELP}") from None
        # The compositor needs a moment to pick up a new device.
        self._ready_at = time.monotonic() + 0.6

    def _wait_ready(self):
        delay = self._ready_at - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    def position(self):
        src = self.pointer_source
        if src is not None:
            pos = src.position()
            if pos is not None and (self._last is None or src.updated >= self._last_t):
                return pos
        return self._last

    def _move(self, x, y):
        self._wait_ready()
        bx, by, w, h = self._bounds
        ax = min(max(x - bx, 0), w - 1)
        ay = min(max(y - by, 0), h - 1)
        ec = self.ec
        self._ptr.write(ec.EV_ABS, ec.ABS_X, ax)
        self._ptr.write(ec.EV_ABS, ec.ABS_Y, ay)
        self._ptr.syn()
        self._last = (x, y)
        self._last_t = time.monotonic()

    def _button(self, button, down):
        self._wait_ready()
        code = self.k.BUTTONS.get(button)
        if code is None:
            raise BackendError(f"Unknown mouse button: {button}")
        self._ptr.write(self.ec.EV_KEY, code, 1 if down else 0)
        self._ptr.syn()

    def _scroll(self, dx, dy):
        self._wait_ready()
        ec = self.ec
        if dy:
            self._ptr.write(ec.EV_REL, ec.REL_WHEEL, int(dy))
        if dx:
            self._ptr.write(ec.EV_REL, ec.REL_HWHEEL, int(dx))
        self._ptr.syn()

    def _code(self, key):
        try:
            return self.k.code_for(key)
        except KeyError:
            raise BackendError(f"{keys.display(key)} can't be typed on Wayland") from None

    def _key(self, key, down):
        self._wait_ready()
        code, shift = self._code(key)
        ec = self.ec
        if shift and down:
            self._kbd.write(ec.EV_KEY, ec.KEY_LEFTSHIFT, 1)
        self._kbd.write(ec.EV_KEY, code, 1 if down else 0)
        if shift and not down:
            self._kbd.write(ec.EV_KEY, ec.KEY_LEFTSHIFT, 0)
        self._kbd.syn()

    def _type(self, text):
        self._wait_ready()
        ec = self.ec
        missing = sorted({ch for ch in text if self.k.char_codes(ch) is None})
        if missing:
            raise BackendError("These characters can't be typed on Wayland: " + " ".join(missing))
        for ch in text:
            code, shift = self.k.char_codes(ch)
            if shift:
                self._kbd.write(ec.EV_KEY, ec.KEY_LEFTSHIFT, 1)
            self._kbd.write(ec.EV_KEY, code, 1)
            self._kbd.syn()
            self._kbd.write(ec.EV_KEY, code, 0)
            if shift:
                self._kbd.write(ec.EV_KEY, ec.KEY_LEFTSHIFT, 0)
            self._kbd.syn()
            time.sleep(0.006)

    def listener(self, handler):
        return EvdevListener(self, handler)

    def close(self):
        super().close()
        for dev in (self._kbd, self._ptr):
            if dev is not None:
                try:
                    dev.close()
                except Exception:
                    pass
        self._kbd = self._ptr = None


def tap_to_click_enabled() -> bool:
    """Best guess at whether the desktop turns touchpad taps into clicks."""
    import shutil
    import subprocess
    from pathlib import Path
    kde = Path.home() / ".config" / "kcminputrc"
    try:
        text = kde.read_text()
    except OSError:
        text = ""
    if "TapToClick=true" in text:
        return True
    if "TapToClick=false" in text:
        return False
    if shutil.which("gsettings") and "GNOME" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper():
        try:
            out = subprocess.run(["gsettings", "get", "org.gnome.desktop.peripherals.touchpad", "tap-to-click"],
                                 capture_output=True, text=True, timeout=2).stdout.strip()
            return out != "false"
        except (OSError, subprocess.SubprocessError):
            pass
    return True


class _Tap:
    """Turns a short, still touch on a touchpad into a click, the way libinput
    does when tap-to-click is on. Those clicks never appear as button events."""

    MAX_S = 0.2

    def __init__(self, dev, ec):
        self.ec = ec
        absinfo = dict(dev.capabilities().get(ec.EV_ABS, []))
        ax = absinfo.get(ec.ABS_X)
        self.slop = max(1, int((ax.max - ax.min) * 0.03)) if ax else 30
        self.reset()

    def reset(self):
        self.t0 = None
        self.fingers = 0
        self.start = [None, None]
        self.moved = False
        self.pressed = False

    def feed(self, ev):
        """Returns a button name when a tap completes."""
        ec = self.ec
        if ev.type == ec.EV_KEY:
            if ev.code == ec.BTN_TOUCH:
                if ev.value == 1:
                    self.reset()
                    self.t0 = ev.timestamp()
                    self.fingers = max(self.fingers, 1)
                elif self.t0 is not None:
                    tap = (not self.moved and not self.pressed and ev.timestamp() - self.t0 <= self.MAX_S)
                    fingers = self.fingers
                    self.reset()
                    if tap:
                        return {1: "left", 2: "right", 3: "middle"}.get(fingers)
            elif ev.value == 1 and ev.code == ec.BTN_TOOL_DOUBLETAP:
                self.fingers = max(self.fingers, 2)
            elif ev.value == 1 and ev.code == ec.BTN_TOOL_TRIPLETAP:
                self.fingers = max(self.fingers, 3)
            elif ev.code in (ec.BTN_LEFT, ec.BTN_RIGHT, ec.BTN_MIDDLE):
                self.pressed = True
        elif ev.type == ec.EV_ABS and self.t0 is not None and ev.code in (ec.ABS_X, ec.ABS_Y):
            i = 0 if ev.code == ec.ABS_X else 1
            if self.start[i] is None:
                self.start[i] = ev.value
            elif abs(ev.value - self.start[i]) > self.slop:
                self.moved = True
        return None


class EvdevListener(Listener):
    """Reads every keyboard and mouse under /dev/input, without grabbing them."""

    RESCAN_S = 2.0

    def __init__(self, backend: UinputBackend, handler):
        super().__init__(handler)
        self.backend = backend
        self.evdev = backend.evdev
        self.ec = backend.ec
        self._thread = None
        self._stop = threading.Event()
        self._wake_r = self._wake_w = -1
        self._devices = {}
        self._taps = {}
        self._tapping = tap_to_click_enabled()
        self._unsubscribe = None

    def _wanted(self, dev) -> bool:
        if dev.name.startswith(DEVICE_PREFIX):
            return False
        caps = dev.capabilities()
        key_caps = set(caps.get(self.ec.EV_KEY, []))
        rel_caps = set(caps.get(self.ec.EV_REL, []))
        return bool(key_caps & {self.ec.KEY_A, self.ec.KEY_ENTER, self.ec.KEY_F1, self.ec.BTN_LEFT}
                    or self.ec.REL_WHEEL in rel_caps)

    def _scan(self, sel):
        denied = 0
        found = 0
        for path in self.evdev.list_devices():
            if path in self._devices:
                continue
            try:
                dev = self.evdev.InputDevice(path)
            except PermissionError:
                denied += 1
                continue
            except OSError:
                continue
            if not self._wanted(dev):
                dev.close()
                continue
            self._devices[path] = dev
            keys_ = set(dev.capabilities().get(self.ec.EV_KEY, []))
            direct = self.ec.INPUT_PROP_DIRECT in dev.input_props()
            if self._tapping and not direct and {self.ec.BTN_TOOL_FINGER, self.ec.BTN_TOUCH} <= keys_:
                self._taps[dev.fd] = _Tap(dev, self.ec)
            sel.register(dev.fd, selectors.EVENT_READ, dev)
            found += 1
        return found, denied

    def start(self):
        self._stop.clear()
        self._wake_r, self._wake_w = os.pipe()
        sel = selectors.DefaultSelector()
        found, denied = self._scan(sel)
        if not self._devices:
            sel.close()
            for fd in (self._wake_r, self._wake_w):
                os.close(fd)
            self._wake_r = self._wake_w = -1
            if denied:
                raise BackendError(SETUP_HELP)
            raise BackendError("No keyboard or mouse found under /dev/input.")
        sel.register(self._wake_r, selectors.EVENT_READ, None)
        self._thread = threading.Thread(target=self._run, args=(sel,), name="evdev-listener", daemon=True)
        self._thread.start()

    def _run(self, sel):
        ec = self.ec
        k = self.backend.k
        next_scan = time.monotonic() + self.RESCAN_S
        try:
            while not self._stop.is_set():
                for skey, _ in sel.select(timeout=0.5):
                    dev = skey.data
                    if dev is None:
                        os.read(self._wake_r, 64)
                        continue
                    try:
                        events = list(dev.read())
                    except (OSError, BlockingIOError) as err:
                        if isinstance(err, BlockingIOError):
                            continue
                        sel.unregister(dev.fd)
                        self._taps.pop(dev.fd, None)
                        self._devices.pop(dev.path, None)
                        try:
                            dev.close()
                        except Exception:
                            pass
                        continue
                    tap = self._taps.get(dev.fd)
                    for ev in events:
                        if tap is not None:
                            button = tap.feed(ev)
                            if button:
                                pos = self.backend.position() or (None, None)
                                now = time.monotonic()
                                self.emit(InputEvent("button", now, x=pos[0], y=pos[1], button=button, down=True))
                                self.emit(InputEvent("button", now + 0.01, x=pos[0], y=pos[1], button=button, down=False))
                                continue
                        if ev.type == ec.EV_KEY:
                            if ev.value == 2:
                                continue
                            down = ev.value == 1
                            if ev.code in k.BUTTON_TO_NAME:
                                pos = self.backend.position() or (None, None)
                                self.emit(InputEvent("button", time.monotonic(), x=pos[0], y=pos[1],
                                                     button=k.BUTTON_TO_NAME[ev.code], down=down))
                            elif ev.code in k.CODE_TO_NAME:
                                self.emit(InputEvent("key", time.monotonic(), key=k.CODE_TO_NAME[ev.code], down=down))
                        elif ev.type == ec.EV_REL and ev.code in (ec.REL_WHEEL, ec.REL_HWHEEL):
                            pos = self.backend.position() or (None, None)
                            dy = ev.value if ev.code == ec.REL_WHEEL else 0
                            dx = ev.value if ev.code == ec.REL_HWHEEL else 0
                            self.emit(InputEvent("scroll", time.monotonic(), x=pos[0], y=pos[1], dx=dx, dy=dy))
                if time.monotonic() >= next_scan:
                    self._scan(sel)
                    next_scan = time.monotonic() + self.RESCAN_S
        finally:
            for dev in list(self._devices.values()):
                try:
                    dev.close()
                except Exception:
                    pass
            self._devices.clear()
            sel.close()

    def want_moves(self, on: bool):
        source = self.backend.pointer_source
        if on and self._unsubscribe is None and source is not None:
            self._unsubscribe = source.subscribe(
                lambda x, y: self.emit(InputEvent("move", time.monotonic(), x=x, y=y)))
        elif not on and self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    def stop(self):
        self._stop.set()
        self.want_moves(False)
        if self._wake_w >= 0:
            try:
                os.write(self._wake_w, b"x")
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        for fd in (self._wake_r, self._wake_w):
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
        self._wake_r = self._wake_w = -1
