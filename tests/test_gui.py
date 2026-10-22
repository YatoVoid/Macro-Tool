import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPointF, QSettings, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent, QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from fakes import FakeBackend  # noqa: E402
from macrotool import model  # noqa: E402
from macrotool.backends import InputEvent  # noqa: E402
from macrotool.model import Step  # noqa: E402


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(app, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from PySide6.QtCore import QStandardPaths
    QStandardPaths.setTestModeEnabled(True)
    from macrotool.window import MainWindow
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    monkeypatch.setattr("macrotool.window.config_dir", lambda: tmp_path)
    w = MainWindow(FakeBackend(), settings=settings)
    w.opt_countdown.setChecked(False)
    w.opt_awake.setChecked(False)
    yield w
    w.dirty = False
    w.close()


def pump(app, seconds=0.0):
    end = time.monotonic() + seconds
    while True:
        app.processEvents()
        if time.monotonic() >= end:
            return
        time.sleep(0.005)


def wait_until(app, cond, timeout=5):
    end = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < end, "timed out"
        pump(app, 0.01)


def test_window_starts_ready(win):
    assert win.state_label.text() == "Ready"
    assert win.play_btn.text().startswith("Start")
    assert "F9" in win.play_btn.text()
    assert win.hub.active
    assert not win.stop_btn.isEnabled()


def test_clicker_runs_and_stops(win, app):
    win.tabs.setCurrentIndex(0)
    win._set_interval(5)
    win.ac_point.setPoint((40, 50))
    win.start_playing(from_button=False)
    assert win.playing and win.stop_btn.isEnabled() and not win.play_btn.isEnabled()
    pump(app, 0.3)
    win.stop_all()
    wait_until(app, lambda: not win.playing)
    moves = win.backend.entries("move")
    assert moves and set(moves) == {("move", 40, 50)}
    assert not win.backend.holding
    assert "Stopped" in win.statusBar().currentMessage()


def test_clicker_count_finishes(win, app):
    win.tabs.setCurrentIndex(0)
    win._set_interval(1)
    win.ac_times_rb.setChecked(True)
    win.ac_times.setValue(25)
    win.start_playing(from_button=False)
    wait_until(app, lambda: not win.playing)
    assert len([e for e in win.backend.entries("button") if e[2]]) == 25
    assert "Finished: 25 clicks" in win.statusBar().currentMessage()


def test_hotkeys_toggle_playback(win, app):
    win.tabs.setCurrentIndex(0)
    win._set_interval(50)
    hub = win.hub
    hub._on_event(InputEvent("key", 0, key="f9", down=True))
    hub._on_event(InputEvent("key", 0, key="f9", down=False))
    wait_until(app, lambda: win.playing)
    hub._on_event(InputEvent("key", 0, key="f9", down=True))
    hub._on_event(InputEvent("key", 0, key="f9", down=False))
    wait_until(app, lambda: not win.playing)


def test_edit_undo_redo_and_save(win, app, tmp_path):
    win.tabs.setCurrentIndex(1)
    win._change([Step("click", x=1, y=2), Step("key", key="a")], "Add", [1])
    win._change(win.steps_model.steps + [Step("wait", delay_ms=500)], "Add", [2])
    assert len(win.steps_model.steps) == 3
    win.undo.undo()
    assert len(win.steps_model.steps) == 2
    win.undo.redo()
    assert win.steps_model.steps[2].kind == "wait"
    assert win.windowTitle().startswith("*")

    win.table.selectRow(0)
    win.duplicate_steps()
    assert [s.kind for s in win.steps_model.steps] == ["click", "click", "key", "wait"]
    win.table.clearSelection()
    win.table.selectRow(3)
    win.move_steps(-1)
    assert [s.kind for s in win.steps_model.steps] == ["click", "click", "wait", "key"]
    win._drop_rows([3], 0)
    assert [s.kind for s in win.steps_model.steps] == ["key", "click", "click", "wait"]
    win.table.clearSelection()
    win.table.selectRow(0)
    win.remove_steps()
    assert [s.kind for s in win.steps_model.steps] == ["click", "click", "wait"]

    path = tmp_path / "saved.json"
    assert win._write(path)
    assert not win.windowTitle().startswith("*")
    loaded = model.load(path)
    assert [s.kind for s in loaded.steps] == ["click", "click", "wait"]


def test_skip_checkbox(win, app):
    win._change([Step("click"), Step("key", key="b")], "Add", [])
    idx = win.steps_model.index(1, 0)
    win.steps_model.setData(idx, Qt.Unchecked.value, Qt.CheckStateRole)
    assert not win.steps_model.steps[1].enabled
    win.undo.undo()
    assert win.steps_model.steps[1].enabled


def test_macro_plays_and_highlights(win, app):
    win.tabs.setCurrentIndex(1)
    win._change([Step("click", x=3, y=4, delay_ms=5), Step("key", key="ctrl+s", delay_ms=5)], "Add", [])
    win.m_times.setValue(4)
    win.start_playing(from_button=False)
    wait_until(app, lambda: not win.playing)
    assert len(win.backend.entries("move")) == 4
    assert ("key", "ctrl", True) in win.backend.entries()
    assert win.steps_model.current == -1


def test_playback_error_is_shown(win, app, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    win.backend.fail_on = "type"
    win.tabs.setCurrentIndex(1)
    win._change([Step("text", text="x")], "Add", [])
    win.start_playing(from_button=False)
    wait_until(app, lambda: not win.playing)
    assert shown and "type failed" in shown[0]


def test_countdown_then_play(win, app):
    win.opt_countdown.setChecked(True)
    win.tabs.setCurrentIndex(0)
    win._set_interval(100)
    win._countdown.setInterval(20)
    win.start_playing(from_button=True)
    assert win.state_label.text().startswith("Starting in 3")
    assert win.stop_btn.isEnabled()
    wait_until(app, lambda: win.playing)
    win.stop_all()
    wait_until(app, lambda: not win.playing)


def test_countdown_can_be_cancelled(win, app):
    win.opt_countdown.setChecked(True)
    win.start_playing(from_button=True)
    win.stop_all()
    pump(app, 0.1)
    assert not win.is_busy and win.player is None


def test_recording_through_hub(win, app):
    win.tabs.setCurrentIndex(1)
    win.toggle_recording(from_button=False)
    assert win.hub.recording and "Recording" in win.state_label.text()
    t = time.monotonic()
    for ev in (InputEvent("button", t, x=10, y=20, button="left", down=True),
               InputEvent("button", t + 0.05, x=10, y=20, button="left", down=False),
               InputEvent("key", t + 0.2, key="z", down=True),
               InputEvent("key", t + 0.25, key="z", down=False),
               InputEvent("key", t + 0.3, key="f8", down=True)):
        win.hub._on_event(ev)
    wait_until(app, lambda: not win.hub.recording)
    assert [(s.kind, s.key) for s in win.steps_model.steps] == [("click", ""), ("key", "z")]
    assert (win.steps_model.steps[0].x, win.steps_model.steps[0].y) == (10, 20)


def test_session_survives_restart(app, tmp_path, monkeypatch):
    from macrotool.window import MainWindow
    monkeypatch.setattr("macrotool.window.config_dir", lambda: tmp_path)
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)
    w = MainWindow(FakeBackend(), settings=settings)
    w._change([Step("click", x=9, y=9)], "Add", [])
    w.m_forever.setChecked(True)
    w.dirty = False
    w.close()
    w2 = MainWindow(FakeBackend(), settings=settings)
    assert w2.steps_model.steps == [Step("click", x=9, y=9)]
    assert w2.m_forever.isChecked()
    w2.dirty = False
    w2.close()


def test_open_bad_file_warns(win, tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    p = tmp_path / "bad.json"
    p.write_text("{")
    assert win.open_path(p) is False
    assert "JSON" in shown[0]


def test_open_legacy_file(win, tmp_path):
    import json
    p = tmp_path / "old.json"
    p.write_text(json.dumps({"mode": "multi", "items": [
        {"x": 1, "y": 2, "delay_ms": 3, "unit": "s", "action_type": "left", "key_char": None}]}))
    assert win.open_path(p)
    assert win.steps_model.steps == [Step("click", x=1, y=2, delay_ms=3000)]
    assert win.m_forever.isChecked()


# ---------------------------------------------------------------- picker

def mouse(widget, kind, pos, button=Qt.LeftButton):
    types = {"move": QEvent.MouseMove, "press": QEvent.MouseButtonPress, "release": QEvent.MouseButtonRelease}
    buttons = Qt.NoButton if kind == "move" else button
    pt = QPointF(*pos)
    ev = QMouseEvent(types[kind], pt, pt, pt,
                     button if kind != "move" else Qt.NoButton, buttons, Qt.NoModifier)
    QApplication.sendEvent(widget, ev)


def key(widget, k, mods=Qt.NoModifier):
    QApplication.sendEvent(widget, QKeyEvent(QEvent.KeyPress, k, mods))


def run_picker(app, mode, actions, points=None):
    from macrotool.picker import Picker
    p = Picker(mode, points, native_pixels=True, screenshots={})
    out = []
    p.finished.connect(out.append)
    p.show()
    pump(app, 0.05)
    actions(p.windows[0])
    wait_until(app, lambda: out)
    return out[0]


def test_picker_single_click(app):
    res = run_picker(app, "single", lambda w: (mouse(w, "move", (100, 120)), mouse(w, "press", (100, 120))))
    assert res == [(100, 120)]


def test_picker_nudge_with_keys(app):
    def act(w):
        mouse(w, "move", (100, 100))
        key(w, Qt.Key_Right)
        key(w, Qt.Key_Down, Qt.ShiftModifier)
        key(w, Qt.Key_Return)
    assert run_picker(app, "single", act) == [(101, 110)]


def test_picker_multi_and_undo(app):
    def act(w):
        for p in ((10, 10), (20, 20), (30, 30)):
            mouse(w, "move", p)
            mouse(w, "press", p)
        key(w, Qt.Key_Backspace)
        mouse(w, "press", (5, 5), Qt.RightButton)
    assert run_picker(app, "multi", act) == [(10, 10), (20, 20)]


def test_picker_edit_drag(app):
    def act(w):
        mouse(w, "move", (52, 50))
        mouse(w, "press", (52, 50))
        mouse(w, "move", (300, 200))
        mouse(w, "release", (300, 200))
        key(w, Qt.Key_Return)
    assert run_picker(app, "edit", act, points=[(50, 50), (400, 400)]) == [(300, 200), (400, 400)]


def test_picker_escape_cancels(app):
    assert run_picker(app, "single", lambda w: key(w, Qt.Key_Escape)) is None


def test_picker_paints_with_and_without_screenshot(app):
    from PySide6.QtGui import QPixmap
    from macrotool.picker import Picker
    shot = QPixmap(app.primaryScreen().geometry().size())
    shot.fill(Qt.darkCyan)
    for shots in ({}, {app.primaryScreen().name(): shot}):
        p = Picker("multi", [(10, 10), (60, 60)], screenshots=shots)
        p.show()
        mouse(p.windows[0], "move", (30, 30))
        img = p.windows[0].grab()
        assert not img.isNull()
        p.cancel()


# ---------------------------------------------------------------- dialogs

def test_key_capture(app):
    from macrotool.widgets import KeyCapture
    cap = KeyCapture()
    key(cap, Qt.Key_S, Qt.ControlModifier | Qt.ShiftModifier)
    assert cap.combo() == "ctrl+shift+s"
    key(cap, Qt.Key_F7)
    assert cap.combo() == "f7"
    key(cap, Qt.Key_Exclam, Qt.ShiftModifier)
    assert cap.combo() == "shift+1"


def test_step_dialog_validation(app):
    from macrotool.widgets import StepDialog
    dlg = StepDialog(Step("key", key="a"))
    dlg.keycap.setCombo("")
    dlg.accept()
    assert dlg.result() == 0 and "Press a key" in dlg.error.text()
    dlg.keycap.setCombo("ctrl+v")
    dlg.delay.setValue(1500)
    dlg.accept()
    assert dlg.result() == 1
    assert dlg.step == Step("key", key="ctrl+v", delay_ms=1500)

    dlg.deleteLater()

    dlg = StepDialog(Step("text", text="x"), full_unicode=False)
    dlg.text.setPlainText("héllo")
    dlg.accept()
    assert dlg.result() == 0 and "é" in dlg.error.text()
    dlg.deleteLater()
    pump(app)


def test_duration_edit_units(app):
    from macrotool.widgets import DurationEdit
    d = DurationEdit(90_000)
    assert d.unit.currentText() == "s" and d.value() == 90_000
    d = DurationEdit(7_200_000)
    assert d.unit.currentText() == "h" and d.value() == 7_200_000
    d.unit.setCurrentText("min")
    d.num.setValue(1.5)
    assert d.value() == 90_000


def test_hotkey_dialog_rejects_clashes(app):
    from macrotool.hub import HOTKEY_LABELS
    from macrotool.widgets import HotkeysDialog
    dlg = HotkeysDialog({"play": "f9", "stop": "f9", "record": "f8"}, HOTKEY_LABELS)
    dlg.accept()
    assert dlg.result() == 0 and "share" in dlg.error.text()
    dlg.edits["stop"].setCombo("a")
    dlg.accept()
    assert dlg.result() == 0 and "alone" in dlg.error.text()
    dlg.edits["stop"].setCombo("ctrl+a")
    dlg.accept()
    assert dlg.result() == 1
