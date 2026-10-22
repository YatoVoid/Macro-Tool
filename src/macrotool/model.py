"""Macros, their steps, and the JSON file format."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

from . import keys

FORMAT = "macro-tool"
VERSION = 2

BUTTONS = ("left", "right", "middle", "back", "forward")

# kind -> human label
KINDS = {
    "click": "Click",
    "mouse_down": "Mouse down",
    "mouse_up": "Mouse up",
    "move": "Move",
    "scroll": "Scroll",
    "key": "Key",
    "key_down": "Key down",
    "key_up": "Key up",
    "text": "Type text",
    "wait": "Wait",
}

MAX_DELAY_MS = 24 * 3600 * 1000


class MacroError(ValueError):
    pass


@dataclass
class Step:
    kind: str
    x: int | None = None
    y: int | None = None
    button: str = "left"
    clicks: int = 1
    dx: int = 0
    dy: int = 0
    key: str = ""
    text: str = ""
    delay_ms: int = 0
    duration_ms: int = 0
    enabled: bool = True

    def copy(self) -> "Step":
        return replace(self)

    @property
    def has_point(self) -> bool:
        return self.x is not None and self.y is not None

    def to_dict(self) -> dict:
        out = {"kind": self.kind}
        defaults = Step(self.kind)
        for f in fields(self):
            if f.name == "kind":
                continue
            value = getattr(self, f.name)
            if value != getattr(defaults, f.name) or (f.name in ("x", "y") and value is not None):
                out[f.name] = value
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "Step":
        if not isinstance(data, dict):
            raise MacroError("a step must be an object")
        kind = data.get("kind")
        if kind not in KINDS:
            raise MacroError(f"unknown step kind: {kind!r}")
        step = cls(kind)
        for f in fields(cls):
            if f.name == "kind" or f.name not in data:
                continue
            setattr(step, f.name, data[f.name])
        step.validate()
        return step

    def validate(self):
        def as_int(name, lo, hi, optional=False):
            value = getattr(self, name)
            if value is None and optional:
                return
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise MacroError(f"{self.kind}: {name} must be a number")
            value = int(value)
            if not lo <= value <= hi:
                raise MacroError(f"{self.kind}: {name} must be between {lo} and {hi}")
            setattr(self, name, value)

        as_int("x", -100_000, 100_000, optional=True)
        as_int("y", -100_000, 100_000, optional=True)
        if (self.x is None) != (self.y is None):
            raise MacroError(f"{self.kind}: set both x and y, or neither")
        as_int("clicks", 1, 3)
        as_int("dx", -1000, 1000)
        as_int("dy", -1000, 1000)
        as_int("delay_ms", 0, MAX_DELAY_MS)
        as_int("duration_ms", 0, 600_000)
        self.enabled = bool(self.enabled)
        if self.button not in BUTTONS:
            raise MacroError(f"{self.kind}: unknown mouse button {self.button!r}")
        if not isinstance(self.text, str) or not isinstance(self.key, str):
            raise MacroError(f"{self.kind}: key and text must be strings")
        if self.kind == "key":
            self.key = keys.combo_str(keys.parse_combo(self.key))
        elif self.kind in ("key_down", "key_up"):
            self.key = keys.normalize(self.key)
        if self.kind == "move" and not self.has_point:
            raise MacroError("move: needs a point")
        if self.kind == "text" and not self.text:
            raise MacroError("text: nothing to type")
        if self.kind == "scroll" and not (self.dx or self.dy):
            raise MacroError("scroll: set an amount")

    def describe(self) -> tuple[str, str, str]:
        """(action, where, details) for display."""
        where = f"{self.x}, {self.y}" if self.has_point else "pointer"
        k = self.kind
        side = "" if self.button == "left" else f"{self.button.capitalize()} "
        if k == "click":
            name = {1: "click", 2: "double-click", 3: "triple-click"}[self.clicks]
            action = f"{side}{name}" if side else name.capitalize()
            return action, where, ""
        if k in ("mouse_down", "mouse_up"):
            return f"{side or 'Left '}button {'down' if k == 'mouse_down' else 'up'}", where, ""
        if k == "move":
            return "Move", where, f"glide {self.duration_ms} ms" if self.duration_ms else ""
        if k == "scroll":
            parts = []
            if self.dy:
                parts.append(f"{abs(self.dy)} {'up' if self.dy > 0 else 'down'}")
            if self.dx:
                parts.append(f"{abs(self.dx)} {'right' if self.dx > 0 else 'left'}")
            return "Scroll", where, ", ".join(parts)
        if k == "key":
            return "Key", "", keys.format_combo(self.key)
        if k in ("key_down", "key_up"):
            return KINDS[k], "", keys.display(self.key)
        if k == "text":
            shown = self.text if len(self.text) <= 40 else self.text[:39] + "…"
            return "Type text", "", shown.replace("\n", "⏎")
        return "Wait", "", ""


@dataclass
class Macro:
    steps: list[Step] = field(default_factory=list)
    repeat: int = 1  # 0 means until stopped
    stop_after_s: float = 0.0  # 0 means no time limit
    speed: float = 1.0
    jitter_ms: int = 0
    jitter_px: int = 0

    def copy(self) -> "Macro":
        return replace(self, steps=[s.copy() for s in self.steps])

    def to_dict(self) -> dict:
        return {
            "format": FORMAT,
            "version": VERSION,
            "repeat": self.repeat,
            "stop_after_s": self.stop_after_s,
            "speed": self.speed,
            "jitter_ms": self.jitter_ms,
            "jitter_px": self.jitter_px,
            "steps": [s.to_dict() for s in self.steps],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Macro":
        if not isinstance(data, dict):
            raise MacroError("not a macro file")
        if "mode" in data and "format" not in data:
            return from_legacy(data)
        if data.get("format") != FORMAT:
            raise MacroError("not a Macro Tool file")
        if not isinstance(data.get("version"), int) or data["version"] > VERSION:
            raise MacroError("this file was made by a newer Macro Tool")
        steps = data.get("steps", [])
        if not isinstance(steps, list):
            raise MacroError("steps must be a list")
        macro = cls(steps=[Step.from_dict(s) for s in steps])
        try:
            macro.repeat = max(0, min(int(data.get("repeat", 1)), 10_000_000))
            macro.stop_after_s = max(0.0, min(float(data.get("stop_after_s", 0)), 30 * 86400.0))
            macro.speed = max(0.1, min(float(data.get("speed", 1.0)), 100.0))
            macro.jitter_ms = max(0, min(int(data.get("jitter_ms", 0)), 60_000))
            macro.jitter_px = max(0, min(int(data.get("jitter_px", 0)), 500))
        except (TypeError, ValueError) as e:
            raise MacroError(f"bad setting: {e}") from None
        return macro


def from_legacy(data: dict) -> Macro:
    """Convert a config saved by the old single-file AutoClicker.py."""
    steps: list[Step] = []
    mode = data.get("mode")

    def action(item: dict) -> Step:
        delay = item.get("delay_ms", 500)
        if item.get("unit") == "s":
            delay = delay * 1000
        if item.get("action_type") == "key":
            key = item.get("key_char") or ""
            return Step("key", key=key, delay_ms=int(delay)) if key else Step("wait", delay_ms=int(delay))
        button = item.get("action_type", "left")
        return Step("click", x=item.get("x"), y=item.get("y"),
                    button=button if button in BUTTONS else "left", delay_ms=int(delay))

    try:
        if mode == "single" and data.get("single"):
            steps.append(action(data["single"]))
            repeat = 0
        elif mode == "record":
            repeat = 0
            pending = 0
            for ev in data.get("recorded_events") or []:
                kind, *rest = ev
                gap = int(rest[-1])
                if steps:
                    steps[-1].delay_ms += gap
                else:
                    pending += gap
                if kind == "move":
                    steps.append(Step("move", x=rest[0], y=rest[1]))
                elif kind == "click":
                    x, y, btn, pressed = rest[:4]
                    steps.append(Step("mouse_down" if pressed else "mouse_up", x=x, y=y,
                                      button=btn if btn in BUTTONS else "left"))
                elif kind == "scroll":
                    x, y, dx, dy = rest[:4]
                    if dx or dy:
                        steps.append(Step("scroll", x=x, y=y, dx=int(dx), dy=int(dy)))
                elif kind == "key":
                    steps.append(Step("key", key=keys.normalize(rest[0])))
            if pending and steps:
                steps.insert(0, Step("wait", delay_ms=pending))
        else:
            repeat = 0
            steps = [action(it) for it in data.get("items") or []]
        for s in steps:
            s.validate()
    except (TypeError, ValueError, IndexError, keys.KeyNameError) as e:
        raise MacroError(f"could not read the old config: {e}") from None
    return Macro(steps=steps, repeat=repeat)


def load(path: str | os.PathLike) -> Macro:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise MacroError(f"not valid JSON (line {e.lineno})") from None
    return Macro.from_dict(data)


def save(macro: Macro, path: str | os.PathLike):
    """Write atomically so a crash never leaves half a file."""
    path = Path(path)
    text = json.dumps(macro.to_dict(), indent=2, ensure_ascii=False) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
