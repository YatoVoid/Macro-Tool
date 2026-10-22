"""Key names shared by every backend.

A key is a lowercase name: a printable character ("a", "7", "/"), or a named
key ("enter", "f9", "ctrl"). A combo joins keys with "+", modifiers first:
"ctrl+shift+s". Every backend translates these names to its own codes.
"""

from __future__ import annotations

MODIFIERS = ("ctrl", "alt", "shift", "super")

# Right-hand modifiers count as the left one when matching hotkeys.
MODIFIER_OF = {
    "ctrl": "ctrl", "ctrl_r": "ctrl",
    "alt": "alt", "alt_r": "alt", "alt_gr": "alt",
    "shift": "shift", "shift_r": "shift",
    "super": "super", "super_r": "super",
}

NAMED = (
    ["enter", "esc", "tab", "space", "backspace", "delete", "insert",
     "home", "end", "page_up", "page_down", "up", "down", "left", "right",
     "caps_lock", "num_lock", "scroll_lock", "print_screen", "pause", "menu",
     "media_play_pause", "media_next", "media_previous",
     "volume_up", "volume_down", "volume_mute"]
    + list(MODIFIER_OF)
    + [f"f{i}" for i in range(1, 25)]
)
NAMED_SET = frozenset(NAMED)

ALIASES = {
    "return": "enter", "escape": "esc", "control": "ctrl", "ctl": "ctrl",
    "ctrl_l": "ctrl", "control_l": "ctrl", "control_r": "ctrl_r",
    "alt_l": "alt", "option": "alt", "altgr": "alt_gr",
    "shift_l": "shift",
    "cmd": "super", "command": "super", "win": "super", "windows": "super",
    "meta": "super", "logo": "super", "cmd_l": "super", "cmd_r": "super_r",
    "super_l": "super", "meta_l": "super", "meta_r": "super_r",
    "pgup": "page_up", "pageup": "page_up", "prior": "page_up",
    "pgdn": "page_down", "pagedown": "page_down", "next": "page_down",
    "del": "delete", "ins": "insert", "bksp": "backspace", "back": "backspace",
    "esc_key": "esc", "spacebar": "space",
    "print": "print_screen", "prtsc": "print_screen", "printscreen": "print_screen",
    "apps": "menu", "application": "menu",
    "capslock": "caps_lock", "numlock": "num_lock", "scrolllock": "scroll_lock",
    "media_volume_up": "volume_up", "media_volume_down": "volume_down",
    "media_volume_mute": "volume_mute", "mute": "volume_mute",
    "play_pause": "media_play_pause", "media_prev": "media_previous",
    "plus": "+", "minus": "-", "comma": ",", "period": ".", "slash": "/",
}

# US layout: the unshifted key that produces each shifted character.
SHIFTED = {
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7",
    "*": "8", "(": "9", ")": "0", "_": "-", "+": "=", "{": "[", "}": "]",
    "|": "\\", ":": ";", '"': "'", "<": ",", ">": ".", "?": "/", "~": "`",
}


def us_layout_char(ch: str) -> tuple[str, bool] | None:
    """(key, needs_shift) that types ch on a US keyboard, or None."""
    if ch == "\n":
        return "enter", False
    if ch == "\t":
        return "tab", False
    if ch == " ":
        return "space", False
    if len(ch) != 1 or not ch.isascii():
        return None
    if ch.isalpha():
        return ch.lower(), ch.isupper()
    if ch.isdigit() or ch in "-=[]\\;'`,./":
        return ch, False
    if ch in SHIFTED:
        return SHIFTED[ch], True
    return None


class KeyNameError(ValueError):
    pass


def normalize(name: str) -> str:
    """Return the canonical name of one key, or raise KeyNameError."""
    raw = name.strip()
    if raw.startswith("<") and raw.endswith(">") and len(raw) > 2:
        raw = raw[1:-1]
    if len(raw) == 1:
        if not raw.isprintable():
            raise KeyNameError(f"not a key: {name!r}")
        return raw.lower() if raw.isalpha() else raw
    key = raw.lower().replace(" ", "_").replace("-", "_")
    key = ALIASES.get(key, key)
    if len(key) == 1 or key in NAMED_SET:
        return key
    raise KeyNameError(f"unknown key: {name!r}")


def parse_combo(text: str) -> tuple[str, ...]:
    """Parse "Ctrl+Shift+S" into ("ctrl", "shift", "s"), modifiers first."""
    text = text.strip()
    if not text:
        raise KeyNameError("empty key")
    if text == "+":
        return ("+",)
    parts = []
    for i, piece in enumerate(text.split("+")):
        if piece == "" and i > 0:
            parts.append("+")
            continue
        if piece.strip():
            parts.append(normalize(piece))
    if not parts:
        raise KeyNameError(f"not a key: {text!r}")
    mods = [p for p in MODIFIERS if p in parts]
    rest = []
    for p in parts:
        if p not in MODIFIERS and p not in rest:
            rest.append(p)
    if len(rest) > 1:
        raise KeyNameError(f"a combo can have one main key, got {text!r}")
    return tuple(mods + rest)


def format_combo(keys: tuple[str, ...] | str) -> str:
    """Display form: ("ctrl", "s") -> "Ctrl+S"."""
    if isinstance(keys, str):
        keys = parse_combo(keys)
    return "+".join(display(k) for k in keys)


def display(key: str) -> str:
    if len(key) == 1:
        return key.upper()
    special = {"esc": "Esc", "page_up": "Page Up", "page_down": "Page Down",
               "print_screen": "Print Screen", "caps_lock": "Caps Lock",
               "num_lock": "Num Lock", "scroll_lock": "Scroll Lock",
               "ctrl_r": "Right Ctrl", "alt_r": "Right Alt", "alt_gr": "AltGr",
               "shift_r": "Right Shift", "super_r": "Right Super",
               "media_play_pause": "Play/Pause", "media_next": "Next Track",
               "media_previous": "Previous Track"}
    if key in special:
        return special[key]
    if key[0] == "f" and key[1:].isdigit():
        return key.upper()
    return key.replace("_", " ").title()


def combo_str(keys: tuple[str, ...]) -> str:
    return "+".join(keys)


class ComboMatcher:
    """Tracks which keys are held and reports when a combo is pressed.

    A combo fires once when its main key goes down while exactly its modifiers
    are held, so Ctrl+F9 does not also fire F9.
    """

    def __init__(self):
        self.held: set[str] = set()
        self.combos: dict[tuple[str, ...], object] = {}

    def set_combos(self, combos: dict[tuple[str, ...], object]):
        self.combos = dict(combos)

    def press(self, key: str):
        repeat = key in self.held
        self.held.add(key)
        if repeat or key in MODIFIER_OF:
            return None
        mods = {MODIFIER_OF[k] for k in self.held if k in MODIFIER_OF}
        for combo, value in self.combos.items():
            if combo[-1] == key and set(combo[:-1]) == mods:
                return value
        return None

    def release(self, key: str):
        self.held.discard(key)

    def reset(self):
        self.held.clear()
