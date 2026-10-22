"""Dialogs and input widgets."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                               QPushButton, QRadioButton, QSpinBox, QVBoxLayout, QWidget)

from . import keys
from .model import BUTTONS, KINDS, MAX_DELAY_MS, Step

UNITS = [("ms", 1), ("s", 1000), ("min", 60_000), ("h", 3_600_000)]

QT_NAMED = {
    Qt.Key_Return: "enter", Qt.Key_Enter: "enter", Qt.Key_Escape: "esc", Qt.Key_Tab: "tab",
    Qt.Key_Backtab: "tab", Qt.Key_Space: "space", Qt.Key_Backspace: "backspace",
    Qt.Key_Delete: "delete", Qt.Key_Insert: "insert", Qt.Key_Home: "home", Qt.Key_End: "end",
    Qt.Key_PageUp: "page_up", Qt.Key_PageDown: "page_down", Qt.Key_Up: "up", Qt.Key_Down: "down",
    Qt.Key_Left: "left", Qt.Key_Right: "right", Qt.Key_CapsLock: "caps_lock",
    Qt.Key_NumLock: "num_lock", Qt.Key_ScrollLock: "scroll_lock", Qt.Key_Print: "print_screen",
    Qt.Key_Pause: "pause", Qt.Key_Menu: "menu", Qt.Key_MediaPlay: "media_play_pause",
    Qt.Key_MediaTogglePlayPause: "media_play_pause", Qt.Key_MediaNext: "media_next",
    Qt.Key_MediaPrevious: "media_previous", Qt.Key_VolumeUp: "volume_up",
    Qt.Key_VolumeDown: "volume_down", Qt.Key_VolumeMute: "volume_mute",
    Qt.Key_Control: "ctrl", Qt.Key_Shift: "shift", Qt.Key_Alt: "alt", Qt.Key_AltGr: "alt_gr",
    Qt.Key_Meta: "super", Qt.Key_Super_L: "super", Qt.Key_Super_R: "super_r",
}


def qt_key_name(ev: QKeyEvent) -> str | None:
    k = ev.key()
    if k in QT_NAMED:
        return QT_NAMED[k]
    if Qt.Key_F1 <= k <= Qt.Key_F24:
        return f"f{k - Qt.Key_F1 + 1}"
    if Qt.Key_A <= k <= Qt.Key_Z:
        return chr(k).lower()
    if Qt.Key_0 <= k <= Qt.Key_9:
        return chr(k)
    if 0x20 < k < 0x7f:
        ch = chr(k)
        return keys.SHIFTED.get(ch, ch)  # Shift+1 arrives as "!", store the key: 1
    return None


def qt_modifiers(ev: QKeyEvent) -> list[str]:
    m = ev.modifiers()
    out = []
    if m & Qt.ControlModifier:
        out.append("ctrl")
    if m & Qt.AltModifier:
        out.append("alt")
    if m & Qt.ShiftModifier:
        out.append("shift")
    if m & Qt.MetaModifier:
        out.append("super")
    return out


class DurationEdit(QWidget):
    """A number plus a unit; value() is milliseconds."""

    changed = Signal(int)

    def __init__(self, ms: int = 0, parent=None, minimum_ms: int = 0, zero_text: str = ""):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.num = QDoubleSpinBox()
        self.num.setDecimals(0)
        self.num.setRange(0, MAX_DELAY_MS)
        self.num.setAccelerated(True)
        if zero_text:
            self.num.setSpecialValueText(zero_text)
        self.unit = QComboBox()
        for name, _ in UNITS:
            self.unit.addItem(name)
        self.minimum_ms = minimum_ms
        lay.addWidget(self.num, 1)
        lay.addWidget(self.unit)
        self.setValue(ms)
        if ms == 0:
            self.unit.setCurrentIndex(0)
        self.num.valueChanged.connect(lambda _: self.changed.emit(self.value()))
        self.unit.currentIndexChanged.connect(self._unit_changed)
        self.setFocusProxy(self.num)

    def _factor(self) -> int:
        return UNITS[self.unit.currentIndex()][1]

    def _unit_changed(self, _):
        self.num.setDecimals(0 if self._factor() == 1 else 3)
        self.num.setRange(0, MAX_DELAY_MS / self._factor())
        self.changed.emit(self.value())

    def setValue(self, ms: int):
        ms = int(ms)
        if ms == 0:
            self.num.setValue(0)
            return
        idx = 0
        for i, (_, f) in enumerate(UNITS):
            if ms >= f and ms % f == 0:
                idx = i
        self.unit.blockSignals(True)
        self.unit.setCurrentIndex(idx)
        self.unit.blockSignals(False)
        f = UNITS[idx][1]
        self.num.setDecimals(0 if f == 1 else 3)
        self.num.setRange(0, MAX_DELAY_MS / f)
        self.num.setValue(ms / f)

    def value(self) -> int:
        return max(self.minimum_ms, int(round(self.num.value() * self._factor())))


class KeyCapture(QLineEdit):
    """Click it, press a key or shortcut. combo() returns e.g. "ctrl+shift+s"."""

    changed = Signal(str)

    def __init__(self, combo: str = "", single: bool = False, parent=None):
        super().__init__(parent)
        self.single = single
        self.setReadOnly(True)
        self.setPlaceholderText("Click here, then press a key")
        self._combo = ""
        self.setCombo(combo)
        self.setAccessibleName("Key")

    def combo(self) -> str:
        return self._combo

    def setCombo(self, combo: str):
        self._combo = combo or ""
        self.setText(keys.format_combo(self._combo) if self._combo else "")
        self.changed.emit(self._combo)

    def focusInEvent(self, ev):
        super().focusInEvent(ev)
        self.setPlaceholderText("Press a key…")

    def focusOutEvent(self, ev):
        super().focusOutEvent(ev)
        self.setPlaceholderText("Click here, then press a key")
        self.setText(keys.format_combo(self._combo) if self._combo else "")

    def event(self, ev):
        # Take Tab and Backtab too, so they can be recorded as keys.
        if ev.type() == ev.Type.KeyPress:
            self.keyPressEvent(ev)
            return True
        return super().event(ev)

    def keyPressEvent(self, ev: QKeyEvent):
        if ev.isAutoRepeat():
            return
        name = qt_key_name(ev)
        if name is None:
            return
        if self.single:
            self.setCombo(name)
            return
        mods = qt_modifiers(ev)
        if name in keys.MODIFIER_OF:
            self.setText("+".join(keys.display(m) for m in mods) + "+…")
            return
        try:
            self.setCombo(keys.combo_str(keys.parse_combo("+".join(mods + [name]))))
        except keys.KeyNameError:
            pass


class PointEdit(QWidget):
    """Either "wherever the pointer is" or a fixed point with a Pick button."""

    pick_requested = Signal()

    def __init__(self, point: tuple[int, int] | None, allow_pointer=True, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.at_pointer = QRadioButton("Wherever the pointer is")
        self.fixed = QRadioButton("At this point:")
        group = QButtonGroup(self)
        group.addButton(self.at_pointer)
        group.addButton(self.fixed)
        row = QHBoxLayout()
        self.x = QSpinBox()
        self.y = QSpinBox()
        for s, name in ((self.x, "x"), (self.y, "y")):
            s.setRange(-100_000, 100_000)
            s.setPrefix(f"{name} ")
            s.setAccessibleName(name)
        self.pick_btn = QPushButton("Pick on screen…")
        self.pick_btn.clicked.connect(self.pick_requested)
        row.addWidget(self.fixed)
        row.addWidget(self.x)
        row.addWidget(self.y)
        row.addWidget(self.pick_btn)
        row.addStretch()
        if allow_pointer:
            lay.addWidget(self.at_pointer)
        else:
            self.at_pointer.hide()
        lay.addLayout(row)
        self.fixed.toggled.connect(self._sync)
        self.setPoint(point, allow_pointer)

    def _sync(self):
        on = self.fixed.isChecked()
        self.x.setEnabled(on)
        self.y.setEnabled(on)

    def setPoint(self, point, allow_pointer=True):
        if point is None and allow_pointer:
            self.at_pointer.setChecked(True)
        else:
            self.fixed.setChecked(True)
            if point is not None:
                self.x.setValue(point[0])
                self.y.setValue(point[1])
        self._sync()

    def point(self) -> tuple[int, int] | None:
        if self.fixed.isChecked():
            return self.x.value(), self.y.value()
        return None


class StepDialog(QDialog):
    """Edit one step. pick is a callable returning a native point or None."""

    def __init__(self, step: Step, pick=None, parent=None, full_unicode=True):
        super().__init__(parent)
        self.step = step.copy()
        self.pick = pick
        self.full_unicode = full_unicode
        self.setWindowTitle(f"{KINDS[step.kind]} step")
        self.setMinimumWidth(460)
        form = QFormLayout(self)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        k = step.kind
        self.point = None
        self.button = None
        self.keycap = None
        self.text = None
        self.error = QLabel()
        self.error.setStyleSheet("color: #c0392b")
        self.error.setWordWrap(True)
        self.error.hide()

        if k in ("click", "mouse_down", "mouse_up", "move", "scroll"):
            self.point = PointEdit((step.x, step.y) if step.has_point else None,
                                   allow_pointer=(k != "move"))
            self.point.pick_requested.connect(self._pick)
            form.addRow("Where:", self.point)
        if k in ("click", "mouse_down", "mouse_up"):
            self.button = QComboBox()
            for b in BUTTONS:
                self.button.addItem(b.capitalize(), b)
            self.button.setCurrentIndex(BUTTONS.index(step.button))
            form.addRow("Button:", self.button)
        if k == "click":
            self.clicks = QComboBox()
            self.clicks.addItems(["Single click", "Double click", "Triple click"])
            self.clicks.setCurrentIndex(step.clicks - 1)
            form.addRow("Clicks:", self.clicks)
            self.hold = DurationEdit(step.duration_ms)
            form.addRow("Hold the button for:", self.hold)
        if k == "move":
            self.hold = DurationEdit(step.duration_ms)
            form.addRow("Glide there over:", self.hold)
        if k == "scroll":
            self.dy = QSpinBox()
            self.dy.setRange(-1000, 1000)
            self.dy.setValue(step.dy)
            self.dy.setSuffix(" notches (up is positive)")
            self.dx = QSpinBox()
            self.dx.setRange(-1000, 1000)
            self.dx.setValue(step.dx)
            self.dx.setSuffix(" notches (right is positive)")
            form.addRow("Vertical:", self.dy)
            form.addRow("Horizontal:", self.dx)
        if k in ("key", "key_down", "key_up"):
            self.keycap = KeyCapture(step.key, single=(k != "key"))
            clear = QPushButton("Clear")
            clear.clicked.connect(lambda: self.keycap.setCombo(""))
            row = QHBoxLayout()
            row.addWidget(self.keycap, 1)
            row.addWidget(clear)
            form.addRow("Key:" if k != "key" else "Key or shortcut:", row)
            if k == "key":
                self.hold = DurationEdit(step.duration_ms)
                form.addRow("Hold it for:", self.hold)
        if k == "text":
            self.text = QPlainTextEdit(step.text)
            self.text.setTabChangesFocus(True)
            self.text.setMinimumHeight(90)
            form.addRow("Text:", self.text)
            if not full_unicode:
                note = QLabel("On Wayland, text is typed with a US keyboard layout. "
                              "Letters, digits and common symbols work; accented letters and emoji don't.")
                note.setWordWrap(True)
                note.setStyleSheet("color: palette(placeholder-text)")
                form.addRow("", note)
        self.delay = DurationEdit(step.delay_ms)
        form.addRow("Wait:" if k == "wait" else "Then wait:", self.delay)
        form.addRow(self.error)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    def _pick(self):
        if self.pick is None:
            return
        pts = self.pick(self, [(self.point.x.value(), self.point.y.value())] if self.point.fixed.isChecked() else None)
        if pts:
            self.point.setPoint(pts[0])

    def accept(self):
        s = self.step
        if self.point is not None:
            p = self.point.point()
            s.x, s.y = (p if p is not None else (None, None))
        if self.button is not None:
            s.button = self.button.currentData()
        if s.kind == "click":
            s.clicks = self.clicks.currentIndex() + 1
        if s.kind in ("click", "move", "key"):
            s.duration_ms = self.hold.value()
        if s.kind == "scroll":
            s.dx, s.dy = self.dx.value(), self.dy.value()
        if self.keycap is not None:
            s.key = self.keycap.combo()
            if not s.key:
                return self._fail("Press a key first.")
        if self.text is not None:
            s.text = self.text.toPlainText()
            if not self.full_unicode:
                bad = sorted({c for c in s.text if keys.us_layout_char(c) is None})
                if bad:
                    return self._fail("These characters can't be typed on Wayland: " + " ".join(bad))
        s.delay_ms = self.delay.value()
        try:
            s.validate()
        except ValueError as e:
            msg = str(e).split(": ", 1)[-1]
            return self._fail(msg[0].upper() + msg[1:] + ".")
        super().accept()

    def _fail(self, msg):
        self.error.setText(msg)
        self.error.show()


class HotkeysDialog(QDialog):
    def __init__(self, hotkeys: dict[str, str], labels: dict[str, str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Hotkeys")
        self.setMinimumWidth(420)
        form = QFormLayout(self)
        intro = QLabel("These work anywhere, even when Macro Tool is in the background.")
        intro.setWordWrap(True)
        form.addRow(intro)
        self.edits: dict[str, KeyCapture] = {}
        for action, label in labels.items():
            cap = KeyCapture(hotkeys.get(action, ""))
            clear = QPushButton("Clear")
            clear.clicked.connect(lambda _=False, c=cap: c.setCombo(""))
            row = QHBoxLayout()
            row.addWidget(cap, 1)
            row.addWidget(clear)
            form.addRow(label + ":", row)
            self.edits[action] = cap
        self.error = QLabel()
        self.error.setStyleSheet("color: #c0392b")
        self.error.hide()
        form.addRow(self.error)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.RestoreDefaults)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        btns.button(QDialogButtonBox.RestoreDefaults).clicked.connect(self._defaults)
        form.addRow(btns)

    def _defaults(self):
        from .hub import DEFAULT_HOTKEYS
        for action, cap in self.edits.items():
            cap.setCombo(DEFAULT_HOTKEYS.get(action, ""))

    def values(self) -> dict[str, str]:
        return {a: c.combo() for a, c in self.edits.items()}

    def accept(self):
        vals = [v for v in self.values().values() if v]
        if len(vals) != len(set(vals)):
            self.error.setText("Two actions can't share a hotkey.")
            self.error.show()
            return
        plain = [v for v in vals if len(keys.parse_combo(v)) == 1 and len(v) == 1]
        if plain:
            self.error.setText(f"{keys.format_combo(plain[0])} alone would fire every time you type it. "
                               "Use a function key or add Ctrl or Alt.")
            self.error.show()
            return
        super().accept()
