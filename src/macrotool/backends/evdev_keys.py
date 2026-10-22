"""Key names to Linux input event codes (US layout for characters)."""

from __future__ import annotations

from evdev import ecodes as e

from .. import keys

NAMED = {
    "enter": e.KEY_ENTER, "esc": e.KEY_ESC, "tab": e.KEY_TAB, "space": e.KEY_SPACE,
    "backspace": e.KEY_BACKSPACE, "delete": e.KEY_DELETE, "insert": e.KEY_INSERT,
    "home": e.KEY_HOME, "end": e.KEY_END, "page_up": e.KEY_PAGEUP,
    "page_down": e.KEY_PAGEDOWN, "up": e.KEY_UP, "down": e.KEY_DOWN,
    "left": e.KEY_LEFT, "right": e.KEY_RIGHT, "caps_lock": e.KEY_CAPSLOCK,
    "num_lock": e.KEY_NUMLOCK, "scroll_lock": e.KEY_SCROLLLOCK,
    "print_screen": e.KEY_SYSRQ, "pause": e.KEY_PAUSE, "menu": e.KEY_COMPOSE,
    "media_play_pause": e.KEY_PLAYPAUSE, "media_next": e.KEY_NEXTSONG,
    "media_previous": e.KEY_PREVIOUSSONG, "volume_up": e.KEY_VOLUMEUP,
    "volume_down": e.KEY_VOLUMEDOWN, "volume_mute": e.KEY_MUTE,
    "ctrl": e.KEY_LEFTCTRL, "ctrl_r": e.KEY_RIGHTCTRL,
    "alt": e.KEY_LEFTALT, "alt_r": e.KEY_RIGHTALT, "alt_gr": e.KEY_RIGHTALT,
    "shift": e.KEY_LEFTSHIFT, "shift_r": e.KEY_RIGHTSHIFT,
    "super": e.KEY_LEFTMETA, "super_r": e.KEY_RIGHTMETA,
}
for _i in range(1, 25):
    NAMED[f"f{_i}"] = getattr(e, f"KEY_F{_i}")

CHARS = {c: getattr(e, f"KEY_{c.upper()}") for c in "abcdefghijklmnopqrstuvwxyz0123456789"}
CHARS.update({
    "-": e.KEY_MINUS, "=": e.KEY_EQUAL, "[": e.KEY_LEFTBRACE, "]": e.KEY_RIGHTBRACE,
    "\\": e.KEY_BACKSLASH, ";": e.KEY_SEMICOLON, "'": e.KEY_APOSTROPHE,
    "`": e.KEY_GRAVE, ",": e.KEY_COMMA, ".": e.KEY_DOT, "/": e.KEY_SLASH,
    " ": e.KEY_SPACE,
})

CODE_TO_NAME: dict[int, str] = {}
for _name, _code in {**CHARS, **NAMED}.items():
    if _name == " " or _name == "alt_gr":
        continue
    CODE_TO_NAME.setdefault(_code, _name)
for _code, _name in ((e.KEY_KPENTER, "enter"), (e.KEY_KP0, "0"), (e.KEY_KP1, "1"),
                     (e.KEY_KP2, "2"), (e.KEY_KP3, "3"), (e.KEY_KP4, "4"), (e.KEY_KP5, "5"),
                     (e.KEY_KP6, "6"), (e.KEY_KP7, "7"), (e.KEY_KP8, "8"), (e.KEY_KP9, "9"),
                     (e.KEY_KPMINUS, "-"), (e.KEY_KPPLUS, "+"), (e.KEY_KPASTERISK, "*"),
                     (e.KEY_KPSLASH, "/"), (e.KEY_KPDOT, ".")):
    CODE_TO_NAME[_code] = _name

BUTTONS = {"left": e.BTN_LEFT, "right": e.BTN_RIGHT, "middle": e.BTN_MIDDLE,
           "back": e.BTN_SIDE, "forward": e.BTN_EXTRA}
BUTTON_TO_NAME = {v: k for k, v in BUTTONS.items()}
BUTTON_TO_NAME[e.BTN_BACK] = "back"
BUTTON_TO_NAME[e.BTN_FORWARD] = "forward"

ALL_KEY_CODES = sorted(set(NAMED.values()) | set(CHARS.values()))


def code_for(key: str) -> tuple[int, bool]:
    """(code, needs_shift) for a key name; shifted characters use the US layout."""
    if key in NAMED:
        return NAMED[key], False
    if key in CHARS:
        return CHARS[key], False
    if key in keys.SHIFTED:
        return CHARS[keys.SHIFTED[key]], True
    if len(key) == 1 and key.isalpha() and key.lower() in CHARS:
        return CHARS[key.lower()], True
    raise KeyError(key)


def char_codes(ch: str) -> tuple[int, bool] | None:
    if ch == "\n":
        return e.KEY_ENTER, False
    if ch == "\t":
        return e.KEY_TAB, False
    if ch.isupper() and ch.lower() in CHARS:
        return CHARS[ch.lower()], True
    try:
        return code_for(ch)
    except KeyError:
        return None
