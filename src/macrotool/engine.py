"""Plays a macro on a background thread."""

from __future__ import annotations

import random
import threading
import time
import traceback
from dataclasses import dataclass
from typing import Callable

from . import keys
from .backends import Backend
from .model import Macro, Step
from .timing import PreciseTimers


@dataclass
class Progress:
    loop: int  # 1-based
    step: int  # index into macro.steps
    started: float  # monotonic
    actions: int  # steps performed so far


@dataclass
class Result:
    reason: str  # done, stopped, error
    loops: int
    actions: int
    seconds: float
    error: str = ""


class Player:
    """Runs one macro once or in a loop until it ends or stop() is called.

    Waits are measured against deadlines on the monotonic clock, so a 100 ms
    loop stays at 10 per second over hours instead of drifting by the time
    each action takes. Whatever is held down when playback ends is released.
    """

    MIN_GAP_S = 0.001  # never spin without yielding, even at delay 0

    def __init__(self, backend: Backend, macro: Macro,
                 on_progress: Callable[[Progress], None] | None = None,
                 on_finish: Callable[[Result], None] | None = None,
                 progress_interval_s: float = 0.1,
                 rng: random.Random | None = None):
        self.backend = backend
        self.macro = macro.copy()
        self.steps = [s for s in self.macro.steps if s.enabled]
        self.on_progress = on_progress
        self.on_finish = on_finish
        self.progress_interval_s = progress_interval_s
        self.rng = rng or random.Random()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.loops = 0
        self.actions = 0
        self.started = 0.0
        self.result: Result | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.running:
            return
        self._thread = threading.Thread(target=self._run, name="macro-player", daemon=True)
        self._thread.start()

    def stop(self, wait_s: float = 2.0) -> bool:
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=wait_s)
            return not self._thread.is_alive()
        return True

    def run_blocking(self) -> Result:
        self._run()
        return self.result

    def _wait_until(self, deadline: float) -> bool:
        """Sleep until deadline. False if stopped first.

        Most of the wait blocks on the stop event; the last 2 ms are slept in
        small steps, because a blocking wait can overshoot by a whole timer tick.
        """
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return not self._stop.is_set()
            if remaining > 0.003:
                if self._stop.wait(min(remaining - 0.002, 0.25)):
                    return False
            else:
                if self._stop.is_set():
                    return False
                time.sleep(min(remaining, 0.0005))

    def _jitter_point(self, x, y):
        j = self.macro.jitter_px
        if not j:
            return x, y
        return x + self.rng.randint(-j, j), y + self.rng.randint(-j, j)

    def _delay_s(self, ms: int) -> float:
        d = ms
        if self.macro.jitter_ms and ms:
            d = max(0, ms + self.rng.randint(-self.macro.jitter_ms, self.macro.jitter_ms))
        return d / 1000.0 / self.macro.speed

    def _glide(self, x: int, y: int, duration_s: float):
        start = self.backend.position()
        if start is None or duration_s <= 0:
            self.backend.move(x, y)
            return
        sx, sy = start
        t0 = time.monotonic()
        while not self._stop.is_set():
            k = min(1.0, (time.monotonic() - t0) / duration_s)
            k = k * k * (3 - 2 * k)
            self.backend.move(round(sx + (x - sx) * k), round(sy + (y - sy) * k))
            if k >= 1.0:
                return
            self._stop.wait(1 / 120)

    def perform(self, step: Step):
        b = self.backend
        k = step.kind
        if step.has_point and k != "wait":
            x, y = self._jitter_point(step.x, step.y)
            if k == "move":
                self._glide(x, y, step.duration_ms / 1000.0 / self.macro.speed)
                return
            b.move(x, y)
        if k == "click":
            b.click(step.button, step.clicks, hold_s=step.duration_ms / 1000.0)
        elif k == "mouse_down":
            b.button(step.button, True)
        elif k == "mouse_up":
            b.button(step.button, False)
        elif k == "scroll":
            b.scroll(step.dx, step.dy)
        elif k == "key":
            combo = keys.parse_combo(step.key)
            if step.duration_ms:
                for key in combo:
                    b.key(key, True)
                self._stop.wait(step.duration_ms / 1000.0)
                for key in reversed(combo):
                    b.key(key, False)
            else:
                b.combo(combo)
        elif k == "key_down":
            b.key(step.key, True)
        elif k == "key_up":
            b.key(step.key, False)
        elif k == "text":
            b.type_text(step.text)

    def _run(self):
        with PreciseTimers():
            self._play()

    def _play(self):
        self.started = time.monotonic()
        m = self.macro
        reason, error = "done", ""
        last_report = 0.0
        end_at = self.started + m.stop_after_s if m.stop_after_s else None
        try:
            if not self.steps:
                reason = "done"
                return
            deadline = time.monotonic()
            while m.repeat == 0 or self.loops < m.repeat:
                self.loops += 1
                for i, step in enumerate(self.steps):
                    if self._stop.is_set():
                        reason = "stopped"
                        return
                    if end_at is not None and time.monotonic() >= end_at:
                        return
                    if step.kind != "wait":
                        self.perform(step)
                        self.actions += 1
                    now = time.monotonic()
                    if self.on_progress and now - last_report >= self.progress_interval_s:
                        last_report = now
                        self.on_progress(Progress(self.loops, i, self.started, self.actions))
                    # A late step (the system stalled, a long glide) restarts
                    # the schedule from now instead of firing a burst to catch up.
                    deadline = max(deadline + self._delay_s(step.delay_ms), now + self.MIN_GAP_S)
                    if end_at is not None:
                        deadline_capped = min(deadline, end_at)
                        if not self._wait_until(deadline_capped):
                            reason = "stopped"
                            return
                        if deadline_capped < deadline:
                            return
                    elif not self._wait_until(deadline):
                        reason = "stopped"
                        return
        except Exception as e:
            reason = "error"
            error = str(e) or type(e).__name__
            traceback.print_exc()
        finally:
            try:
                self.backend.release_all()
            except Exception:
                pass
            if reason == "done" and self._stop.is_set():
                reason = "stopped"
            self.result = Result(reason, self.loops, self.actions, time.monotonic() - self.started, error)
            if self.on_finish:
                self.on_finish(self.result)
