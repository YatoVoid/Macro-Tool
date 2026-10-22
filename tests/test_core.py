import json
import random
import threading
import time

import pytest

from fakes import FakeBackend
from macrotool import keys, model
from macrotool.backends import InputEvent
from macrotool.engine import Player
from macrotool.model import Macro, MacroError, Step
from macrotool.recorder import Recorder, compact, strip_hotkeys, to_steps


@pytest.mark.parametrize("text,expected", [
    ("F9", ("f9",)),
    ("<f9>", ("f9",)),
    ("Ctrl+Shift+S", ("ctrl", "shift", "s")),
    ("shift+ctrl+s", ("ctrl", "shift", "s")),
    ("cmd+c", ("super", "c")),
    ("ctrl++", ("ctrl", "+")),
    ("+", ("+",)),
    ("Return", ("enter",)),
    ("A", ("a",)),
    ("page down", ("page_down",)),
])
def test_parse_combo(text, expected):
    assert keys.parse_combo(text) == expected


@pytest.mark.parametrize("bad", ["", "ctrl+a+b", "nosuchkey", "\x03"])
def test_parse_combo_rejects(bad):
    with pytest.raises(keys.KeyNameError):
        keys.parse_combo(bad)


def test_format_combo():
    assert keys.format_combo("ctrl+shift+page_up") == "Ctrl+Shift+Page Up"
    assert keys.format_combo("f10") == "F10"


def test_matcher_exact_modifiers():
    m = keys.ComboMatcher()
    m.set_combos({("f9",): "start", ("ctrl", "f9"): "ctrl-start"})
    assert m.press("f9") == "start"
    assert m.press("f9") is None  # auto-repeat
    m.release("f9")
    m.press("ctrl_r")
    assert m.press("f9") == "ctrl-start"
    m.release("f9")
    m.release("ctrl_r")
    m.press("shift")
    assert m.press("f9") is None


def test_step_roundtrip_and_validation():
    s = Step("click", x=10, y=20, button="right", clicks=2, delay_ms=150)
    d = s.to_dict()
    assert d == {"kind": "click", "x": 10, "y": 20, "button": "right", "clicks": 2, "delay_ms": 150}
    assert Step.from_dict(d) == s
    assert Step.from_dict({"kind": "key", "key": "Ctrl+C"}).key == "ctrl+c"
    for bad in ({"kind": "nope"}, {"kind": "click", "x": 1}, {"kind": "click", "clicks": 9},
                {"kind": "click", "button": "thumb"}, {"kind": "move"}, {"kind": "text", "text": ""},
                {"kind": "click", "delay_ms": "fast"}, {"kind": "key", "key": "a+b"}):
        with pytest.raises((MacroError, keys.KeyNameError)):
            Step.from_dict(bad)


def test_macro_save_load(tmp_path):
    m = Macro(steps=[Step("click", x=1, y=2, delay_ms=5), Step("text", text="hé\n"),
                     Step("scroll", dy=-3), Step("wait", delay_ms=1000)],
              repeat=0, stop_after_s=60, speed=2.0, jitter_ms=10, jitter_px=2)
    path = tmp_path / "m.json"
    model.save(m, path)
    assert model.load(path) == m
    assert not list(tmp_path.glob("*.tmp"))


def test_load_errors(tmp_path):
    p = tmp_path / "x.json"
    p.write_text("{nope")
    with pytest.raises(MacroError, match="JSON"):
        model.load(p)
    p.write_text(json.dumps({"format": "macro-tool", "version": 99, "steps": []}))
    with pytest.raises(MacroError, match="newer"):
        model.load(p)
    p.write_text("[]")
    with pytest.raises(MacroError):
        model.load(p)


def test_legacy_multi_and_single():
    legacy = {"mode": "multi", "start_hotkey": "<f9>", "stop_hotkey": "<f10>", "items": [
        {"x": 5, "y": 6, "delay_ms": 2, "unit": "s", "action_type": "right", "key_char": None},
        {"x": 0, "y": 0, "delay_ms": 100, "unit": "ms", "action_type": "key", "key_char": "a"},
    ], "single": None, "recorded_events": []}
    m = Macro.from_dict(legacy)
    assert m.steps == [Step("click", x=5, y=6, button="right", delay_ms=2000), Step("key", key="a", delay_ms=100)]
    assert m.repeat == 0
    single = {"mode": "single", "single": {"x": 3, "y": 4, "delay_ms": 50, "action_type": "left"}}
    assert Macro.from_dict(single).steps == [Step("click", x=3, y=4, delay_ms=50)]


def test_legacy_record():
    legacy = {"mode": "record", "recorded_events": [
        ["click", 10, 10, "left", True, 300], ["click", 10, 10, "left", False, 80],
        ["key", "<enter>", 500], ["scroll", 10, 10, 0, -1, 20]]}
    m = Macro.from_dict(legacy)
    assert [s.kind for s in m.steps] == ["wait", "mouse_down", "mouse_up", "key", "scroll"]
    assert m.steps[0].delay_ms == 300
    assert m.steps[1].delay_ms == 80
    assert m.steps[3].key == "enter"


def run(macro, backend=None, timeout=5, **kw):
    backend = backend or FakeBackend()
    done = threading.Event()
    results = []
    p = Player(backend, macro, on_finish=lambda r: (results.append(r), done.set()), **kw)
    p.start()
    assert done.wait(timeout)
    return backend, results[0], p


def test_player_runs_steps_in_order():
    m = Macro(steps=[Step("click", x=5, y=6), Step("key", key="ctrl+c"), Step("text", text="hi"),
                     Step("scroll", dy=2), Step("mouse_down", button="right"), Step("mouse_up", button="right")])
    b, r, _ = run(m)
    assert r.reason == "done" and r.loops == 1 and r.actions == 6
    assert b.entries() == [
        ("move", 5, 6), ("button", "left", True), ("button", "left", False),
        ("key", "ctrl", True), ("key", "c", True), ("key", "c", False), ("key", "ctrl", False),
        ("type", "hi"), ("scroll", 0, 2), ("button", "right", True), ("button", "right", False)]


def test_player_repeat_count_and_disabled_steps():
    m = Macro(steps=[Step("click"), Step("key", key="a", enabled=False)], repeat=3)
    b, r, _ = run(m)
    assert r.loops == 3
    assert len(b.entries("button")) == 6
    assert not b.entries("key")


def test_player_stop_releases_held_input():
    m = Macro(steps=[Step("key_down", key="shift"), Step("mouse_down"), Step("wait", delay_ms=60_000)], repeat=0)
    b = FakeBackend()
    done = threading.Event()
    p = Player(b, m, on_finish=lambda r: done.set())
    p.start()
    deadline = time.monotonic() + 2
    while len(b.log) < 2 and time.monotonic() < deadline:
        time.sleep(0.005)
    t = time.monotonic()
    assert p.stop()
    assert time.monotonic() - t < 0.5
    assert done.wait(1)
    assert p.result.reason == "stopped"
    assert ("key", "shift", False) in b.entries()
    assert ("button", "left", False) in b.entries()
    assert not b.holding


def test_player_error_reports_and_releases():
    m = Macro(steps=[Step("key_down", key="ctrl"), Step("text", text="x")])
    b, r, _ = run(m, FakeBackend(fail_on="type"))
    assert r.reason == "error" and "type failed" in r.error
    assert ("key", "ctrl", False) in b.entries()


def test_player_keeps_rate_without_drift():
    m = Macro(steps=[Step("click", delay_ms=20)], repeat=50)
    b, r, _ = run(m)
    downs = [e[0] for e in b.log if e[1:] == ("button", "left", True)]
    span = downs[-1] - downs[0]
    assert 49 * 0.020 <= span < 49 * 0.020 + 0.15


def test_player_stop_after_seconds():
    m = Macro(steps=[Step("click", delay_ms=10)], repeat=0, stop_after_s=0.3)
    b, r, _ = run(m)
    assert r.reason == "done"
    assert 0.28 <= r.seconds < 0.6


def test_player_speed_and_zero_delay():
    m = Macro(steps=[Step("click", delay_ms=100)], repeat=5, speed=10)
    _, r, _ = run(m)
    assert r.seconds < 0.3
    m = Macro(steps=[Step("click")], repeat=300)
    _, r, _ = run(m)
    assert r.loops == 300


def test_player_jitter_bounds():
    m = Macro(steps=[Step("click", x=100, y=100)], repeat=200, jitter_px=3)
    b, _, _ = run(m, rng=random.Random(1))
    xs = {e[1] for e in b.entries("move")}
    assert xs <= set(range(97, 104)) and len(xs) > 3


def test_player_empty_macro():
    _, r, _ = run(Macro(steps=[]))
    assert r.reason == "done" and r.actions == 0


def ev(kind, t, **kw):
    return InputEvent(kind, t, **kw)


def test_recorder_compacts_clicks_keys_and_combos():
    events = [
        ev("move", 0.00, x=1, y=1),
        ev("button", 0.10, x=50, y=60, button="left", down=True),
        ev("button", 0.15, x=50, y=60, button="left", down=False),
        ev("button", 0.30, x=50, y=60, button="left", down=True),
        ev("button", 0.35, x=50, y=60, button="left", down=False),
        ev("key", 1.00, key="ctrl", down=True),
        ev("key", 1.10, key="c", down=True),
        ev("key", 1.15, key="c", down=False),
        ev("key", 1.20, key="ctrl", down=False),
        ev("key", 2.00, key="x", down=True),
        ev("key", 2.05, key="x", down=False),
    ]
    steps = compact(to_steps(events, end_t=3.0))
    assert [(s.kind, s.key or s.clicks) for s in steps] == [
        ("move", 1), ("click", 2), ("key", "ctrl+c"), ("key", "x")]
    assert steps[0].delay_ms == 100
    # the double click starts at 0.10 and ctrl+c fires at 1.10; 40 ms of
    # that is spent inside the double click
    assert steps[1].delay_ms == 960
    assert steps[2].delay_ms == 900
    assert steps[3].delay_ms == 1000


def test_recorder_keeps_drags_as_down_move_up():
    events = [ev("button", 0, x=0, y=0, button="left", down=True),
              ev("move", 0.1, x=200, y=0),
              ev("button", 0.2, x=200, y=0, button="left", down=False)]
    kinds = [s.kind for s in compact(to_steps(events, 0.3))]
    assert kinds == ["mouse_down", "move", "mouse_up"]


def test_recorder_strips_hotkeys():
    events = [ev("key", 0, key="f8", down=False),  # released after recording began
              ev("key", 1, key="a", down=True), ev("key", 1.1, key="a", down=False),
              ev("key", 2, key="ctrl", down=True), ev("key", 2.1, key="f8", down=True),
              ev("key", 2.2, key="f8", down=False), ev("key", 2.3, key="ctrl", down=False)]
    kept = strip_hotkeys(events, [("ctrl", "f8"), ("f8",)])
    assert [(e.key, e.down) for e in kept] == [("a", True), ("a", False)]


def test_recorder_throttles_moves_and_caps():
    r = Recorder()
    for i in range(1000):
        r.add(ev("move", i * 0.001, x=i, y=0))
    assert 50 < r.count() < 70
    r2 = Recorder(record_moves=False)
    r2.add(ev("move", 0, x=1, y=1))
    assert r2.count() == 0


def test_recorder_trailing_stop_click():
    r = Recorder()
    r.add(ev("key", 0, key="a", down=True))
    r.add(ev("key", 0.1, key="a", down=False))
    r.add(ev("move", 0.5, x=10, y=10))
    r.add(ev("button", 0.6, x=10, y=10, button="left", down=True))
    steps = r.finish(0.7, drop_trailing_click=True)
    assert [s.kind for s in steps] == ["key"]


def test_recorder_modifier_released_first():
    from macrotool.recorder import reorder_releases
    events = [ev("key", 0, key="ctrl", down=True), ev("key", 0.01, key="a", down=True),
              ev("key", 0.02, key="ctrl", down=False), ev("key", 0.03, key="a", down=False)]
    steps = compact(to_steps(reorder_releases(events), 0.5))
    assert [(s.kind, s.key, s.delay_ms) for s in steps] == [("wait", "", 10), ("key", "ctrl+a", 490)]


def test_touchpad_tap_detection():
    pytest.importorskip("evdev")
    from types import SimpleNamespace
    from evdev import ecodes as ec
    from macrotool.backends.uinput_backend import _Tap

    class Dev:
        def capabilities(self):
            return {ec.EV_ABS: [(ec.ABS_X, SimpleNamespace(min=0, max=1000)), (ec.ABS_Y, SimpleNamespace(min=0, max=600))]}

    def e(t, type_, code, value):
        return SimpleNamespace(type=type_, code=code, value=value, timestamp=lambda: t)

    tap = _Tap(Dev(), ec)
    seq = [e(0, ec.EV_KEY, ec.BTN_TOUCH, 1), e(0, ec.EV_KEY, ec.BTN_TOOL_FINGER, 1),
           e(0.01, ec.EV_ABS, ec.ABS_X, 500), e(0.05, ec.EV_ABS, ec.ABS_X, 505),
           e(0.1, ec.EV_KEY, ec.BTN_TOUCH, 0)]
    assert [tap.feed(x) for x in seq][-1] == "left"
    two = [e(1, ec.EV_KEY, ec.BTN_TOUCH, 1), e(1, ec.EV_KEY, ec.BTN_TOOL_DOUBLETAP, 1),
           e(1.1, ec.EV_KEY, ec.BTN_TOUCH, 0)]
    assert [tap.feed(x) for x in two][-1] == "right"
    swipe = [e(2, ec.EV_KEY, ec.BTN_TOUCH, 1), e(2.01, ec.EV_ABS, ec.ABS_X, 100),
             e(2.05, ec.EV_ABS, ec.ABS_X, 300), e(2.1, ec.EV_KEY, ec.BTN_TOUCH, 0)]
    assert [tap.feed(x) for x in swipe][-1] is None
    slow = [e(3, ec.EV_KEY, ec.BTN_TOUCH, 1), e(3.5, ec.EV_KEY, ec.BTN_TOUCH, 0)]
    assert [tap.feed(x) for x in slow][-1] is None
    click = [e(4, ec.EV_KEY, ec.BTN_TOUCH, 1), e(4.02, ec.EV_KEY, ec.BTN_LEFT, 1),
             e(4.05, ec.EV_KEY, ec.BTN_LEFT, 0), e(4.1, ec.EV_KEY, ec.BTN_TOUCH, 0)]
    assert [tap.feed(x) for x in click][-1] is None


def test_simplify_moves_keeps_path_and_time():
    from macrotool.recorder import simplify_moves
    # straight line right, then straight line down: 41 samples, 10 ms apart
    run = [Step("move", x=i * 5, y=0, delay_ms=10) for i in range(21)]
    run += [Step("move", x=100, y=i * 5, delay_ms=10) for i in range(1, 21)]
    run[-1].delay_ms = 300
    steps = [Step("click", x=0, y=0, delay_ms=50)] + run + [Step("key", key="a")]
    out = simplify_moves(steps)
    moves = [s for s in out if s.kind == "move"]
    assert [(s.x, s.y) for s in moves] == [(0, 0), (100, 0), (100, 100)]
    assert [s.duration_ms for s in moves] == [0, 200, 200]
    assert moves[-1].delay_ms == 300
    total_before = sum(s.delay_ms + s.duration_ms for s in steps)
    total_after = sum(s.delay_ms + s.duration_ms for s in out)
    assert total_before == total_after


def test_merge_typing():
    from macrotool.recorder import merge_typing
    steps = [Step("key", key="shift+c", delay_ms=70), Step("key", key="a", delay_ms=70),
             Step("key", key="space", delay_ms=70), Step("key", key="shift+1", delay_ms=70),
             Step("key", key="enter", delay_ms=10), Step("key", key="ctrl+s", delay_ms=5),
             Step("key", key="x", delay_ms=1)]
    out = merge_typing(steps)
    assert [(s.kind, s.text or s.key, s.delay_ms) for s in out] == [
        ("text", "Ca !", 280), ("key", "enter", 10), ("key", "ctrl+s", 5), ("key", "x", 1)]


def test_us_layout_chars():
    assert keys.us_layout_char("A") == ("a", True)
    assert keys.us_layout_char("!") == ("1", True)
    assert keys.us_layout_char("/") == ("/", False)
    assert keys.us_layout_char("\n") == ("enter", False)
    assert keys.us_layout_char("é") is None


def test_evdev_char_codes_match_layout():
    pytest.importorskip("evdev")
    from evdev import ecodes as e
    from macrotool.backends.evdev_keys import char_codes
    assert char_codes("A") == (e.KEY_A, True)
    assert char_codes("?") == (e.KEY_SLASH, True)
    assert char_codes(" ") == (e.KEY_SPACE, False)
    assert char_codes("ü") is None
