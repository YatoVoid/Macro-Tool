"""Real input on the runner's own desktop (Windows and macOS CI).

Run with MACRO_TOOL_DESKTOP=1. Linux uses test_x11.py instead.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("MACRO_TOOL_DESKTOP") != "1",
                                reason="set MACRO_TOOL_DESKTOP=1 on a desktop session")

HERE = Path(__file__).parent


@pytest.fixture
def target(tmp_path):
    log = tmp_path / "events.jsonl"
    log.touch()
    env = {k: v for k, v in os.environ.items() if k != "QT_QPA_PLATFORM"}
    proc = subprocess.Popen([sys.executable, str(HERE / "support" / "target_app.py"), str(log), "100,100,600,400"],
                            env=env)
    time.sleep(3)
    yield log
    proc.terminate()
    proc.wait(10)


def events(log, kind):
    time.sleep(0.5)
    rows = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    return [r for r in rows if r["type"] == kind]


def test_pointer_moves_and_clicks(target):
    from macrotool.backends.pynput_backend import PynputBackend
    b = PynputBackend()
    b.move(300, 250)
    time.sleep(0.2)
    assert b.position() == (300, 250)
    b.click("left")
    b.move(320, 260)
    b.click("right")
    presses = events(target, "press")
    assert [(p["button"], p["x"], p["y"]) for p in presses] == [("left", 300, 250), ("right", 320, 260)]


def test_typing(target):
    from macrotool.backends.pynput_backend import PynputBackend
    b = PynputBackend()
    b.move(300, 250)
    b.click("left")
    time.sleep(0.3)
    b.type_text("Hello 42")
    typed = "".join(e["text"] for e in events(target, "key"))
    assert typed == "Hello 42"
