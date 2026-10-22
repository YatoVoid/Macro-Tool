"""Turns raw input events into macro steps."""

from __future__ import annotations

import threading

from . import keys
from .backends import InputEvent
from .model import Step

MAX_EVENTS = 500_000
CLICK_MAX_S = 0.5
CLICK_MAX_PX = 4
MOVE_MIN_S = 0.016
MOVE_MIN_PX = 3


class Recorder:
    """Collects events while recording. Thread safe: listeners call add()."""

    def __init__(self, record_moves: bool = True, ignore_combos: list[tuple[str, ...]] | None = None):
        self.record_moves = record_moves
        self.ignore_combos = [tuple(c) for c in (ignore_combos or [])]
        self.events: list[InputEvent] = []
        self._lock = threading.Lock()
        self._last_move: InputEvent | None = None
        self.full = False

    def add(self, ev: InputEvent):
        with self._lock:
            if len(self.events) >= MAX_EVENTS:
                self.full = True
                return
            if ev.kind == "move":
                if not self.record_moves:
                    return
                last = self._last_move
                if last is not None and (ev.t - last.t < MOVE_MIN_S or
                                         (abs(ev.x - last.x) < MOVE_MIN_PX and abs(ev.y - last.y) < MOVE_MIN_PX)):
                    return
                self._last_move = ev
            self.events.append(ev)

    def count(self) -> int:
        with self._lock:
            return len(self.events)

    def finish(self, end_t: float, drop_trailing_click: bool = False) -> list[Step]:
        with self._lock:
            events = list(self.events)
        events = strip_hotkeys(events, self.ignore_combos)
        events = reorder_releases(events)
        if drop_trailing_click:
            events = strip_trailing_click(events)
        return compact(to_steps(events, end_t))


def strip_hotkeys(events: list[InputEvent], combos: list[tuple[str, ...]]) -> list[InputEvent]:
    """Remove the key events that made up a hotkey press (start/stop recording)."""
    if not combos:
        return events
    mains: dict[str, list[set[str]]] = {}
    for c in combos:
        mains.setdefault(c[-1], []).append(set(c[:-1]))
    drop: set[int] = set()
    held: dict[str, int] = {}
    for i, ev in enumerate(events):
        if ev.kind != "key":
            continue
        if ev.down:
            held.setdefault(ev.key, i)
            if ev.key in mains:
                mods = {keys.MODIFIER_OF[k] for k in held if k in keys.MODIFIER_OF}
                if mods in mains[ev.key]:
                    drop.add(i)
                    for k, j in held.items():
                        if k in keys.MODIFIER_OF:
                            drop.add(j)
                    _drop_release(events, i, ev.key, drop)
                    for k in list(held):
                        if k in keys.MODIFIER_OF:
                            _drop_release(events, i, k, drop)
        else:
            held.pop(ev.key, None)
    # A hotkey's release may arrive with no press recorded (pressed before start).
    first_down: dict[str, bool] = {}
    for i, ev in enumerate(events):
        if ev.kind == "key" and i not in drop:
            if ev.down:
                first_down.setdefault(ev.key, True)
            elif ev.key not in first_down:
                drop.add(i)
                first_down[ev.key] = True
    return [ev for i, ev in enumerate(events) if i not in drop]


def _drop_release(events, start, key, drop):
    for j in range(start + 1, len(events)):
        ev = events[j]
        if ev.kind == "key" and ev.key == key and not ev.down:
            drop.add(j)
            return


def reorder_releases(events: list[InputEvent], window_s: float = 0.25) -> list[InputEvent]:
    """Ctrl down, A down, Ctrl up, A up: people (and xdotool) often let go of
    the modifier first. Move A's release in front so it reads as Ctrl+A."""
    events = list(events)
    held: set[str] = set()
    i = 0
    while i < len(events):
        ev = events[i]
        if ev.kind == "key":
            if ev.down:
                held.add(ev.key)
            elif ev.key in keys.MODIFIER_OF:
                j = i + 1
                while (j < len(events) and events[j].kind == "key" and not events[j].down
                       and events[j].t - ev.t <= window_s):
                    later = events[j]
                    if later.key not in keys.MODIFIER_OF and later.key in held:
                        moved = InputEvent("key", ev.t, key=later.key, down=False)
                        del events[j]
                        events.insert(i, moved)
                        ev = events[i]
                        break
                    j += 1
                held.discard(ev.key)
            else:
                held.discard(ev.key)
        i += 1
    return events


def strip_trailing_click(events: list[InputEvent]) -> list[InputEvent]:
    """Drop the click on the app's own Stop button and anything after it."""
    for i in range(len(events) - 1, -1, -1):
        ev = events[i]
        if ev.kind == "button" and ev.down:
            # also drop the pointer's trip over to the button
            while i > 0 and events[i - 1].kind == "move":
                i -= 1
            return events[:i]
    return events


def to_steps(events: list[InputEvent], end_t: float) -> list[Step]:
    steps: list[Step] = []
    times: list[float] = []
    for ev in events:
        if ev.kind == "move":
            if ev.x is None:
                continue
            steps.append(Step("move", x=ev.x, y=ev.y))
        elif ev.kind == "button":
            kind = "mouse_down" if ev.down else "mouse_up"
            if ev.x is None:
                steps.append(Step(kind, button=ev.button))
            else:
                steps.append(Step(kind, x=ev.x, y=ev.y, button=ev.button))
        elif ev.kind == "scroll":
            if not (ev.dx or ev.dy):
                continue
            if ev.x is None:
                steps.append(Step("scroll", dx=ev.dx, dy=ev.dy))
            else:
                steps.append(Step("scroll", x=ev.x, y=ev.y, dx=ev.dx, dy=ev.dy))
        elif ev.kind == "key":
            steps.append(Step("key_down" if ev.down else "key_up", key=ev.key))
        else:
            continue
        times.append(ev.t)
    for i, step in enumerate(steps):
        nxt = times[i + 1] if i + 1 < len(steps) else end_t
        step.delay_ms = max(0, int(round((nxt - times[i]) * 1000)))
    return steps


def compact(steps: list[Step]) -> list[Step]:
    """Merge press/release pairs into clicks and key taps, and taps under
    held modifiers into combos like ctrl+c."""
    out: list[Step] = []
    i = 0
    while i < len(steps):
        s = steps[i]
        nxt = steps[i + 1] if i + 1 < len(steps) else None
        if (s.kind == "mouse_down" and nxt is not None and nxt.kind == "mouse_up"
                and nxt.button == s.button and s.delay_ms <= CLICK_MAX_S * 1000
                and _close(s, nxt)):
            click = Step("click", x=s.x, y=s.y, button=s.button, delay_ms=s.delay_ms + nxt.delay_ms)
            if s.x is None and nxt.x is not None:
                click.x, click.y = nxt.x, nxt.y
            out.append(click)
            i += 2
            continue
        if (s.kind == "key_down" and nxt is not None and nxt.kind == "key_up"
                and nxt.key == s.key and s.key not in keys.MODIFIER_OF):
            out.append(Step("key", key=s.key, delay_ms=s.delay_ms + nxt.delay_ms))
            i += 2
            continue
        out.append(s)
        i += 1
    out = _merge_double_clicks(out)
    out = _merge_combos(out)
    out = merge_typing(out)
    return simplify_moves(out)


def _typed_char(step: Step) -> str | None:
    if step.kind != "key" or step.duration_ms:
        return None
    combo = keys.parse_combo(step.key)
    if combo == ("space",):
        return " "
    if len(combo) == 1 and len(combo[0]) == 1:
        return combo[0]
    if len(combo) == 2 and combo[0] == "shift" and len(combo[1]) == 1:
        ch = combo[1]
        if ch.isalpha():
            return ch.upper()
        unshifted = {v: k for k, v in keys.SHIFTED.items()}
        return unshifted.get(ch)
    return None


def merge_typing(steps: list[Step], min_chars: int = 2) -> list[Step]:
    """Runs of plain typing become one Type text step."""
    out: list[Step] = []
    i = 0
    while i < len(steps):
        j = i
        chars = []
        while j < len(steps):
            ch = _typed_char(steps[j])
            if ch is None:
                break
            chars.append(ch)
            j += 1
        if len(chars) >= min_chars:
            out.append(Step("text", text="".join(chars), delay_ms=sum(s.delay_ms for s in steps[i:j])))
            i = j
        else:
            out.append(steps[i])
            i += 1
    return out


def simplify_moves(steps: list[Step], tolerance_px: float = 4.0) -> list[Step]:
    """Shrink each run of recorded pointer moves to the points where the path
    bends. Each kept point glides from the one before over the time the
    original samples took, so playback follows the same path at the same pace."""
    out: list[Step] = []
    i = 0
    while i < len(steps):
        if steps[i].kind != "move":
            out.append(steps[i])
            i += 1
            continue
        j = i
        while j < len(steps) and steps[j].kind == "move" and steps[j].duration_ms == 0:
            j += 1
        if j == i:
            out.append(steps[i])
            i += 1
            continue
        run = steps[i:j]
        if len(run) <= 2:
            out.extend(run)
            i = j
            continue
        times = [0]
        for s in run[:-1]:
            times.append(times[-1] + s.delay_ms)
        keep = _rdp([(s.x, s.y) for s in run], tolerance_px)
        first = run[0].copy()
        first.delay_ms = 0
        merged = [first]
        for a, b in zip(keep, keep[1:]):
            step = run[b].copy()
            step.duration_ms = times[b] - times[a]
            step.delay_ms = 0
            merged.append(step)
        merged[-1].delay_ms = run[-1].delay_ms
        out.extend(merged)
        i = j
    return out


def _rdp(points: list[tuple[int, int]], eps: float) -> list[int]:
    """Indices kept by Ramer-Douglas-Peucker, always including both ends."""
    keep = {0, len(points) - 1}
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        (ax, ay), (bx, by) = points[a], points[b]
        dx, dy = bx - ax, by - ay
        norm = (dx * dx + dy * dy) ** 0.5
        best, best_d = -1, eps
        for k in range(a + 1, b):
            px, py = points[k]
            if norm == 0:
                d = ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
            else:
                d = abs(dy * px - dx * py + bx * ay - by * ax) / norm
            if d > best_d:
                best, best_d = k, d
        if best >= 0:
            keep.add(best)
            stack.append((a, best))
            stack.append((best, b))
    return sorted(keep)


def _close(a: Step, b: Step) -> bool:
    if a.x is None or b.x is None:
        return True
    return abs(a.x - b.x) <= CLICK_MAX_PX and abs(a.y - b.y) <= CLICK_MAX_PX


def _merge_double_clicks(steps: list[Step]) -> list[Step]:
    out: list[Step] = []
    for s in steps:
        prev = out[-1] if out else None
        if (s.kind == "click" and prev is not None and prev.kind == "click"
                and prev.button == s.button and prev.clicks < 3 and prev.delay_ms <= 400
                and _close(prev, s)):
            prev.clicks += 1
            # the double click itself spends about 40 ms between presses
            prev.delay_ms = s.delay_ms + max(0, prev.delay_ms - 40)
            continue
        out.append(s)
    return out


def _merge_combos(steps: list[Step]) -> list[Step]:
    """key_down ctrl, key c, key_up ctrl  ->  key ctrl+c (when nothing else
    happened while ctrl was held)."""
    out: list[Step] = []
    i = 0
    while i < len(steps):
        s = steps[i]
        if s.kind == "key_down" and s.key in keys.MODIFIER_OF:
            j = i
            mods = []
            while j < len(steps) and steps[j].kind == "key_down" and steps[j].key in keys.MODIFIER_OF:
                mods.append(steps[j].key)
                j += 1
            taps = []
            while j < len(steps) and steps[j].kind == "key":
                taps.append(steps[j])
                j += 1
            ups = []
            while j < len(steps) and steps[j].kind == "key_up" and steps[j].key in mods:
                ups.append(steps[j].key)
                j += 1
            if taps and sorted(ups) == sorted(mods) and len(set(keys.MODIFIER_OF[m] for m in mods)) == len(mods):
                names = tuple(keys.MODIFIER_OF[m] for m in mods)
                for n, tap in enumerate(taps):
                    combo = keys.parse_combo("+".join(names + (tap.key,)))
                    delay = tap.delay_ms
                    if n == len(taps) - 1:
                        delay += sum(steps[k].delay_ms for k in range(j - len(ups), j))
                    out.append(Step("key", key=keys.combo_str(combo), delay_ms=delay))
                if out and len(taps):
                    # time spent pressing the modifiers before the first tap
                    lead = sum(steps[k].delay_ms for k in range(i, i + len(mods)))
                    if lead:
                        if len(out) > len(taps):
                            out[-len(taps) - 1].delay_ms += lead
                        else:
                            out.insert(0, Step("wait", delay_ms=lead))
                i = j
                continue
        out.append(s)
        i += 1
    return out
