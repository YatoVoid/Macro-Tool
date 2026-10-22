"""Real input on a real X server.

Run inside Xvfb (no window manager), for example:
    MACRO_TOOL_X11=1 xvfb-run -s "-screen 0 1280x800x24" pytest tests/test_x11.py
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("MACRO_TOOL_X11") != "1" or not os.environ.get("DISPLAY") or shutil.which("xdotool") is None,
    reason="set MACRO_TOOL_X11=1 and run under Xvfb with xdotool installed")

HERE = Path(__file__).parent
ENV = {**os.environ, "QT_QPA_PLATFORM": "xcb", "XDG_SESSION_TYPE": "x11"}
ENV.pop("WAYLAND_DISPLAY", None)


def xdo(*args):
    subprocess.run(["xdotool", *map(str, args)], check=True, env=ENV)


class Target:
    def __init__(self, tmp_path):
        self.path = tmp_path / "events.jsonl"
        self.path.touch()
        self.proc = subprocess.Popen([sys.executable, str(HERE / "support" / "target_app.py"), str(self.path)],
                                     env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.2)
        xdo("mousemove", 640, 400)

    def events(self, kind=None, settle=0.3):
        time.sleep(settle)
        out = [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]
        return [e for e in out if kind is None or e["type"] == kind]

    def clear(self):
        self.path.write_text("")

    def close(self):
        self.proc.terminate()
        self.proc.wait(5)


@pytest.fixture
def target(tmp_path):
    t = Target(tmp_path)
    yield t
    t.close()


@pytest.fixture
def backend():
    os.environ.pop("WAYLAND_DISPLAY", None)
    from macrotool.backends.pynput_backend import PynputBackend
    b = PynputBackend()
    yield b
    b.close()


def test_clicks_land_on_exact_pixels(target, backend):
    backend.move(200, 150)
    backend.click("left")
    backend.move(1001, 777)
    backend.click("right")
    backend.move(5, 5)
    backend.click("middle")
    presses = target.events("press")
    assert [(e["button"], e["x"], e["y"]) for e in presses] == [
        ("left", 200, 150), ("right", 1001, 777), ("middle", 5, 5)]
    assert len(target.events("release")) == 3


def test_typing_and_shortcuts(target, backend):
    backend.type_text("Hello, World! 123")
    backend.combo(("shift", "a"))
    backend.combo(("enter",))
    keys = target.events("key")
    text = "".join(e["text"] for e in keys if e["text"] and e["text"] != "\r")
    assert text == "Hello, World! 123A"
    assert keys[-1]["key"] == 0x01000004  # Qt.Key_Return


def test_scroll(target, backend):
    backend.scroll(0, -3)
    wheels = target.events("wheel")
    assert sum(1 for w in wheels if w["dy"] < 0) == 3


def test_player_rate_with_real_input(target, backend):
    from macrotool.engine import Player
    from macrotool.model import Macro, Step
    m = Macro(steps=[Step("click", x=300, y=300, delay_ms=10)], repeat=200)
    result = Player(backend, m).run_blocking()
    assert result.reason == "done"
    presses = target.events("press", settle=0.6)
    assert len(presses) == 200
    span = presses[-1]["t"] - presses[0]["t"]
    assert 1.95 <= span <= 2.3


def test_listener_and_recorder_roundtrip(target, backend):
    from macrotool import keys
    from macrotool.engine import Player
    from macrotool.model import Macro
    from macrotool.recorder import Recorder

    rec = Recorder(record_moves=False, ignore_combos=[keys.parse_combo("f8")])
    got = []
    listener = backend.listener(lambda ev: (got.append(ev), rec.add(ev)))
    listener.start()
    try:
        time.sleep(0.3)
        xdo("key", "F8")
        xdo("mousemove", 321, 234, "click", 1)
        time.sleep(0.2)
        xdo("type", "--delay", "40", "hi")
        xdo("key", "ctrl+a")
        xdo("key", "F8")
        time.sleep(0.4)
        steps = rec.finish(time.monotonic())
    finally:
        listener.stop()
    assert any(e.kind == "key" and e.key == "f8" and e.down for e in got)
    summary = [(s.kind, s.key or s.text or (s.x, s.y)) for s in steps]
    assert summary == [("click", (321, 234)), ("text", "hi"), ("key", "ctrl+a")]

    target.clear()
    xdo("mousemove", 640, 400)
    Player(backend, Macro(steps=steps)).run_blocking()
    presses = target.events("press")
    assert [(e["x"], e["y"]) for e in presses] == [(321, 234)]
    typed = [e for e in target.events("key") if e["text"]]
    assert [e["text"] for e in typed][:2] == ["h", "i"]
    assert typed[2]["key"] == ord("A") and typed[2]["mods"] & 0x04000000  # Ctrl


def test_cli_play(target, tmp_path):
    from macrotool import model
    from macrotool.model import Macro, Step
    path = tmp_path / "m.json"
    model.save(Macro(steps=[Step("click", x=111, y=222, delay_ms=20), Step("text", text="ok")], repeat=3), path)
    r = subprocess.run([sys.executable, "-m", "macrotool.cli", "play", str(path), "--stop-key", "none"],
                       env=ENV, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert "done: 3 loop(s)" in r.stderr
    assert len(target.events("press")) == 3
    assert "".join(e["text"] for e in target.events("key")) == "okokok"
    r = subprocess.run([sys.executable, "-m", "macrotool.cli", "check", str(path)], env=ENV,
                       capture_output=True, text=True)
    assert r.returncode == 0 and "2 steps" in r.stdout


def test_stop_hotkey_ends_endless_cli_run(target, tmp_path):
    from macrotool import model
    from macrotool.model import Macro, Step
    path = tmp_path / "m.json"
    model.save(Macro(steps=[Step("click", x=50, y=50, delay_ms=50)], repeat=0), path)
    p = subprocess.Popen([sys.executable, "-m", "macrotool.cli", "play", str(path)], env=ENV,
                         stderr=subprocess.PIPE, text=True)
    time.sleep(1.5)
    xdo("key", "F10")
    out = p.communicate(timeout=10)[1]
    assert p.returncode == 0
    assert out.strip().splitlines()[-1].startswith("stopped:")
