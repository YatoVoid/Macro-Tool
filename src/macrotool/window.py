"""The main window."""

from __future__ import annotations

import json
import time
from pathlib import Path

from PySide6.QtCore import (QAbstractTableModel, QByteArray, QMimeData, QModelIndex, QSettings,
                            QStandardPaths, Qt, QTimer, Signal)
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence, QUndoCommand, QUndoStack
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox,
                               QDoubleSpinBox, QFileDialog, QGridLayout, QGroupBox,
                               QHBoxLayout, QHeaderView, QLabel, QMainWindow, QMenu, QMessageBox,
                               QPushButton, QRadioButton, QSpinBox, QTableView, QTabWidget,
                               QToolButton, QVBoxLayout, QWidget)

from . import __version__, keys, model, picker
from .backends import Backend, session_type
from .engine import Player, Progress, Result
from .hub import DEFAULT_HOTKEYS, HOTKEY_LABELS, InputHub
from .keepawake import KeepAwake
from .model import Macro, MacroError, Step
from .widgets import DurationEdit, HotkeysDialog, PointEdit, StepDialog

MIME = "application/x-macrotool-rows"
FILE_FILTER = "Macro Tool files (*.json);;All files (*)"


def fmt_ms(ms: int) -> str:
    if ms <= 0:
        return ""
    if ms < 1000:
        return f"{ms} ms"
    if ms < 60_000:
        s = ms / 1000
        return f"{s:g} s" if s == int(s) or ms % 100 == 0 else f"{s:.2f} s"
    if ms < 3_600_000:
        m, s = divmod(ms // 1000, 60)
        return f"{m} min {s} s" if s else f"{m} min"
    h, rest = divmod(ms // 60_000, 60)
    return f"{h} h {rest} min" if rest else f"{h} h"


def fmt_elapsed(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def config_dir() -> Path:
    path = Path(QStandardPaths.writableLocation(QStandardPaths.AppConfigLocation))
    path.mkdir(parents=True, exist_ok=True)
    return path


class StepsModel(QAbstractTableModel):
    HEADERS = ["#", "Action", "Where", "Details", "Then wait"]
    moved = Signal(list, int)  # source rows, destination row
    toggled = Signal(int, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.steps: list[Step] = []
        self.current = -1

    def set_steps(self, steps: list[Step]):
        self.beginResetModel()
        self.steps = steps
        self.endResetModel()

    def set_current(self, row: int):
        old, self.current = self.current, row
        for r in (old, row):
            if 0 <= r < len(self.steps):
                self.dataChanged.emit(self.index(r, 0), self.index(r, len(self.HEADERS) - 1))

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.steps)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.HEADERS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        s = self.steps[index.row()]
        col = index.column()
        if role == Qt.DisplayRole:
            if col == 0:
                return str(index.row() + 1)
            action, where, details = s.describe()
            if col == 1:
                return action
            if col == 2:
                return where
            if col == 3:
                return details
            if col == 4:
                return fmt_ms(s.delay_ms) if s.kind != "wait" else fmt_ms(s.delay_ms) or "0 ms"
        if role == Qt.CheckStateRole and col == 0:
            return Qt.Checked if s.enabled else Qt.Unchecked
        if role == Qt.ForegroundRole and not s.enabled:
            return QApplication.palette().placeholderText()
        if role == Qt.FontRole and index.row() == self.current:
            f = QApplication.font()
            f.setBold(True)
            return f
        if role == Qt.TextAlignmentRole and col in (0, 4):
            return int(Qt.AlignRight | Qt.AlignVCenter) if col == 4 else int(Qt.AlignLeft | Qt.AlignVCenter)
        if role == Qt.ToolTipRole and col == 0:
            return "Untick to skip this step"
        return None

    def setData(self, index, value, role=Qt.EditRole):
        if role == Qt.CheckStateRole and index.column() == 0:
            on = Qt.CheckState(value) == Qt.Checked
            self.toggled.emit(index.row(), on)
            return True
        return False

    def flags(self, index):
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled
        if not index.isValid():
            return Qt.ItemIsDropEnabled
        if index.column() == 0:
            base |= Qt.ItemIsUserCheckable
        return base

    def supportedDropActions(self):
        return Qt.MoveAction

    def mimeTypes(self):
        return [MIME]

    def mimeData(self, indexes):
        rows = sorted({i.row() for i in indexes})
        data = QMimeData()
        data.setData(MIME, QByteArray(json.dumps(rows).encode()))
        return data

    def canDropMimeData(self, data, action, row, column, parent):
        return data.hasFormat(MIME)

    def dropMimeData(self, data, action, row, column, parent):
        if not data.hasFormat(MIME):
            return False
        rows = json.loads(bytes(data.data(MIME)).decode())
        if row < 0:
            row = parent.row() if parent.isValid() else len(self.steps)
        self.moved.emit(rows, row)
        return False  # the window rebuilds the list; Qt must not remove rows itself


class Snapshot(QUndoCommand):
    def __init__(self, window, before: list[Step], after: list[Step], text: str, select: list[int]):
        super().__init__(text)
        self.window = window
        self.before = [s.copy() for s in before]
        self.after = [s.copy() for s in after]
        self.select = select

    def redo(self):
        self.window._apply_steps([s.copy() for s in self.after], self.select)

    def undo(self):
        self.window._apply_steps([s.copy() for s in self.before], [])


class MainWindow(QMainWindow):
    progress_signal = Signal(object)
    finish_signal = Signal(object)

    def __init__(self, backend: Backend, pointer_note: str = "", settings: QSettings | None = None):
        super().__init__()
        self.backend = backend
        self.settings = settings or QSettings()
        self.hub = InputHub(backend, self)
        self.keepawake = KeepAwake()
        self.player: Player | None = None
        self.play_mode = ""
        self.file: Path | None = None
        self.dirty = False
        self.pointer_note = pointer_note
        self.undo = QUndoStack(self)
        self.macro = Macro()
        self._countdown_left = 0
        self._countdown = QTimer(self)
        self._countdown.setInterval(1000)
        self._countdown.timeout.connect(self._tick_countdown)
        self._record_started = 0.0
        self._record_mode = "replace"
        self._pending: Macro | None = None
        self._last_progress: Progress | None = None
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(500)
        self._status_timer.timeout.connect(self._refresh_status)
        self._autosave = QTimer(self)
        self._autosave.setSingleShot(True)
        self._autosave.setInterval(800)
        self._autosave.timeout.connect(self._save_session)

        self.setWindowTitle("Macro Tool")
        self.setMinimumSize(720, 520)
        self._build_ui()
        self._build_menu()
        self.progress_signal.connect(self._on_progress)
        self.finish_signal.connect(self._on_finish)
        self.hub.hotkey.connect(self._on_hotkey)
        self.undo.cleanChanged.connect(self._on_clean_changed)
        self._load_settings()
        self._start_hub()
        self._sync_buttons()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(12, 10, 12, 8)
        outer.setSpacing(10)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.play_btn = QPushButton()
        self.play_btn.setObjectName("play")
        self.stop_btn = QPushButton()
        self.stop_btn.setObjectName("stop")
        self.record_btn = QPushButton()
        self.record_btn.setObjectName("record")
        for b in (self.play_btn, self.stop_btn, self.record_btn):
            b.setMinimumHeight(34)
            b.setCursor(Qt.PointingHandCursor)
            bar.addWidget(b)
        self.play_btn.clicked.connect(lambda: self.start_playing(from_button=True))
        self.stop_btn.clicked.connect(self.stop_all)
        self.record_btn.clicked.connect(lambda: self.toggle_recording(from_button=True))
        bar.addSpacing(10)
        self.state_label = QLabel()
        self.state_label.setObjectName("state")
        self.state_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bar.addWidget(self.state_label, 1)
        outer.addLayout(bar)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_clicker_tab(), "Auto clicker")
        self.tabs.addTab(self._build_macro_tab(), "Macro")
        self.tabs.currentChanged.connect(lambda _: self._sync_buttons())
        outer.addWidget(self.tabs, 1)

        self.backend_label = QLabel()
        self.statusBar().addPermanentWidget(self.backend_label)
        self.statusBar().setSizeGripEnabled(True)
        self.setStyleSheet("""
            QPushButton#play, QPushButton#stop, QPushButton#record { padding: 4px 14px; font-weight: 600; }
            QPushButton#play:enabled { color: #1f7a3e; }
            QPushButton#record:enabled { color: #b3361f; }
            QLabel#state { font-weight: 600; }
            QLabel.hint { color: palette(placeholder-text); }
        """)

    def _build_clicker_tab(self):
        w = QWidget()
        grid = QGridLayout(w)
        grid.setContentsMargins(14, 14, 14, 14)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(12)
        row = 0

        grid.addWidget(QLabel("Click every:"), row, 0, Qt.AlignRight)
        iv = QHBoxLayout()
        self.ac_h = QSpinBox(); self.ac_h.setRange(0, 999); self.ac_h.setSuffix(" h")
        self.ac_m = QSpinBox(); self.ac_m.setRange(0, 59); self.ac_m.setSuffix(" min")
        self.ac_s = QSpinBox(); self.ac_s.setRange(0, 59); self.ac_s.setSuffix(" s")
        self.ac_ms = QSpinBox(); self.ac_ms.setRange(0, 999); self.ac_ms.setSuffix(" ms")
        for sb, name in ((self.ac_h, "hours"), (self.ac_m, "minutes"), (self.ac_s, "seconds"), (self.ac_ms, "milliseconds")):
            sb.setAccessibleName(name)
            sb.setAccelerated(True)
            iv.addWidget(sb)
        iv.addStretch()
        grid.addLayout(iv, row, 1)
        row += 1

        grid.addWidget(QLabel("Randomize by:"), row, 0, Qt.AlignRight)
        jr = QHBoxLayout()
        self.ac_jitter = QSpinBox(); self.ac_jitter.setRange(0, 60_000); self.ac_jitter.setSuffix(" ms")
        self.ac_jitter.setPrefix("± ")
        self.ac_jitter.setAccessibleName("Randomize interval")
        jr.addWidget(self.ac_jitter)
        hint = QLabel("Varies each wait so the clicks don't land on an exact beat")
        hint.setProperty("class", "hint")
        jr.addWidget(hint)
        jr.addStretch()
        grid.addLayout(jr, row, 1)
        row += 1

        grid.addWidget(QLabel("Mouse button:"), row, 0, Qt.AlignRight)
        br = QHBoxLayout()
        self.ac_button = QComboBox()
        for b in model.BUTTONS:
            self.ac_button.addItem(b.capitalize(), b)
        self.ac_clicks = QComboBox()
        self.ac_clicks.addItems(["Single click", "Double click", "Triple click"])
        br.addWidget(self.ac_button)
        br.addWidget(self.ac_clicks)
        br.addStretch()
        grid.addLayout(br, row, 1)
        row += 1

        grid.addWidget(QLabel("Click at:"), row, 0, Qt.AlignRight | Qt.AlignTop)
        self.ac_point = PointEdit(None)
        self.ac_point.pick_requested.connect(self._pick_clicker_point)
        grid.addWidget(self.ac_point, row, 1)
        row += 1

        grid.addWidget(QLabel("Repeat:"), row, 0, Qt.AlignRight | Qt.AlignTop)
        rr = QVBoxLayout()
        self.ac_forever = QRadioButton("Until stopped")
        self.ac_times_rb = QRadioButton("This many times:")
        g = QButtonGroup(w); g.addButton(self.ac_forever); g.addButton(self.ac_times_rb)
        self.ac_times = QSpinBox(); self.ac_times.setRange(1, 10_000_000); self.ac_times.setValue(100)
        self.ac_forever.setChecked(True)
        t = QHBoxLayout(); t.addWidget(self.ac_times_rb); t.addWidget(self.ac_times); t.addStretch()
        rr.addWidget(self.ac_forever)
        rr.addLayout(t)
        grid.addLayout(rr, row, 1)
        row += 1

        self.ac_rate = QLabel()
        self.ac_rate.setProperty("class", "hint")
        grid.addWidget(self.ac_rate, row, 1)
        row += 1
        grid.setRowStretch(row, 1)
        grid.setColumnStretch(1, 1)
        for sb in (self.ac_h, self.ac_m, self.ac_s, self.ac_ms, self.ac_jitter):
            sb.valueChanged.connect(self._update_rate)
        return w

    def _build_macro_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 10, 10, 10)
        top = QHBoxLayout()

        self.steps_model = StepsModel(self)
        self.steps_model.moved.connect(self._drop_rows)
        self.steps_model.toggled.connect(self._toggle_step)
        self.table = QTableView()
        self.table.setModel(self.steps_model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setDragDropMode(QAbstractItemView.InternalMove)
        self.table.setDragEnabled(True)
        self.table.setAcceptDrops(True)
        self.table.setDropIndicatorShown(True)
        self.table.setDragDropOverwriteMode(False)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(26)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        hh.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        hh.setHighlightSections(False)
        self.table.doubleClicked.connect(lambda _: self.edit_step())
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)
        self.table.selectionModel().selectionChanged.connect(lambda *_: self._sync_buttons())
        self.table.setAccessibleName("Macro steps")

        self.empty_label = QLabel(
            "No steps yet.\n\nPress Record and do the task once, or use Add step to build it by hand.")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setProperty("class", "hint")
        self.empty_label.setWordWrap(True)

        stack = QVBoxLayout()
        stack.addWidget(self.table, 1)
        stack.addWidget(self.empty_label, 1)
        top.addLayout(stack, 1)

        side = QVBoxLayout()
        side.setSpacing(6)
        self.add_btn = QToolButton()
        self.add_btn.setText("Add step")
        self.add_btn.setPopupMode(QToolButton.InstantPopup)
        self.add_btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
        add_menu = QMenu(self)
        add_menu.addAction("Click at a point…", lambda: self.add_click(multi=False))
        add_menu.addAction("Clicks at several points…", lambda: self.add_click(multi=True))
        add_menu.addAction("Click wherever the pointer is", lambda: self.add_step(Step("click", delay_ms=100), edit=False))
        add_menu.addSeparator()
        add_menu.addAction("Key or shortcut…", lambda: self.add_step(Step("key", key="enter", delay_ms=100)))
        add_menu.addAction("Type text…", lambda: self.add_step(Step("text", text=" ", delay_ms=100)))
        add_menu.addAction("Wait…", lambda: self.add_step(Step("wait", delay_ms=1000)))
        add_menu.addAction("Scroll…", lambda: self.add_step(Step("scroll", dy=-3, delay_ms=100)))
        add_menu.addAction("Move the pointer…", lambda: self.add_step(Step("move", x=0, y=0, delay_ms=100), pick_first=True))
        add_menu.addSeparator()
        add_menu.addAction("Hold a key down…", lambda: self.add_step(Step("key_down", key="shift")))
        add_menu.addAction("Release a key…", lambda: self.add_step(Step("key_up", key="shift")))
        add_menu.addAction("Press a mouse button…", lambda: self.add_step(Step("mouse_down")))
        add_menu.addAction("Release a mouse button…", lambda: self.add_step(Step("mouse_up")))
        self.add_btn.setMenu(add_menu)
        self.add_btn.setSizePolicy(self.add_btn.sizePolicy().horizontalPolicy(), self.add_btn.sizePolicy().verticalPolicy())

        self.edit_btn = QPushButton("Edit…")
        self.edit_btn.clicked.connect(self.edit_step)
        self.dup_btn = QPushButton("Duplicate")
        self.dup_btn.clicked.connect(self.duplicate_steps)
        self.del_btn = QPushButton("Remove")
        self.del_btn.clicked.connect(self.remove_steps)
        self.up_btn = QPushButton("Move up")
        self.up_btn.clicked.connect(lambda: self.move_steps(-1))
        self.down_btn = QPushButton("Move down")
        self.down_btn.clicked.connect(lambda: self.move_steps(1))
        self.points_btn = QPushButton("Points on screen…")
        self.points_btn.setToolTip("See every click point on the screen and drag them to new places")
        self.points_btn.clicked.connect(self.edit_points)
        for b in (self.add_btn, self.edit_btn, self.dup_btn, self.del_btn, self.up_btn, self.down_btn):
            b.setMinimumWidth(140)
            side.addWidget(b)
        side.addSpacing(10)
        self.points_btn.setMinimumWidth(140)
        side.addWidget(self.points_btn)
        side.addStretch()
        top.addLayout(side)
        lay.addLayout(top, 1)

        opts = QGroupBox("Playback")
        og = QGridLayout(opts)
        og.setHorizontalSpacing(12)
        self.m_forever = QRadioButton("Repeat until stopped")
        self.m_times_rb = QRadioButton("Play")
        g = QButtonGroup(w); g.addButton(self.m_forever); g.addButton(self.m_times_rb)
        self.m_times = QSpinBox(); self.m_times.setRange(1, 10_000_000); self.m_times.setSuffix(" time")
        self.m_times.valueChanged.connect(lambda v: self.m_times.setSuffix(" time" if v == 1 else " times"))
        self.m_times_rb.setChecked(True)
        rep = QHBoxLayout(); rep.addWidget(self.m_times_rb); rep.addWidget(self.m_times)
        rep.addSpacing(16); rep.addWidget(self.m_forever); rep.addStretch()
        og.addLayout(rep, 0, 0, 1, 4)
        og.addWidget(QLabel("Stop after:"), 1, 0)
        self.m_limit = DurationEdit(0, zero_text="no limit")
        self.m_limit.setValue(0)
        self.m_limit.unit.setCurrentText("min")
        og.addWidget(self.m_limit, 1, 1)
        og.addWidget(QLabel("Speed:"), 1, 2)
        self.m_speed = QDoubleSpinBox(); self.m_speed.setRange(0.1, 100); self.m_speed.setSingleStep(0.25)
        self.m_speed.setSuffix("×"); self.m_speed.setValue(1.0)
        og.addWidget(self.m_speed, 1, 3)
        og.addWidget(QLabel("Randomize waits:"), 2, 0)
        self.m_jitter_ms = QSpinBox(); self.m_jitter_ms.setRange(0, 60_000); self.m_jitter_ms.setPrefix("± "); self.m_jitter_ms.setSuffix(" ms")
        og.addWidget(self.m_jitter_ms, 2, 1)
        og.addWidget(QLabel("Randomize points:"), 2, 2)
        self.m_jitter_px = QSpinBox(); self.m_jitter_px.setRange(0, 500); self.m_jitter_px.setPrefix("± "); self.m_jitter_px.setSuffix(" px")
        og.addWidget(self.m_jitter_px, 2, 3)
        self.m_moves = QCheckBox("Record mouse movement (needed for drags and hover menus)")
        og.addWidget(self.m_moves, 3, 0, 1, 4)
        og.setColumnStretch(4, 1)
        for wdg in (self.m_times, self.m_speed, self.m_jitter_ms, self.m_jitter_px):
            wdg.valueChanged.connect(self._options_changed)
        self.m_limit.changed.connect(self._options_changed)
        self.m_forever.toggled.connect(self._options_changed)
        lay.addWidget(opts)
        return w

    def _build_menu(self):
        mb = self.menuBar()
        f = mb.addMenu("&File")
        f.addAction("&New", self.new_file, QKeySequence.New)
        f.addAction("&Open…", self.open_dialog, QKeySequence.Open)
        self.recent_menu = f.addMenu("Open &recent")
        f.addAction("&Save", self.save, QKeySequence.Save)
        f.addAction("Save &as…", self.save_as, QKeySequence.SaveAs)
        f.addSeparator()
        f.addAction("&Quit", self.close, QKeySequence.Quit)

        e = mb.addMenu("&Edit")
        undo = self.undo.createUndoAction(self, "&Undo")
        undo.setShortcut(QKeySequence.Undo)
        redo = self.undo.createRedoAction(self, "&Redo")
        redo.setShortcut(QKeySequence.Redo)
        e.addAction(undo)
        e.addAction(redo)
        e.addSeparator()
        self.act_edit = e.addAction("&Edit step…", self.edit_step)
        self.act_dup = e.addAction("D&uplicate", self.duplicate_steps, QKeySequence("Ctrl+D"))
        self.act_del = e.addAction("Re&move", self.remove_steps, QKeySequence.Delete)
        self.act_del.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        self.table.addAction(self.act_del)
        e.addAction("Select &all steps", self.table.selectAll)

        o = mb.addMenu("&Options")
        o.addAction("&Hotkeys…", self.edit_hotkeys)
        o.addSeparator()
        self.opt_countdown = QAction("Count down 3 seconds when started with the button", self, checkable=True)
        self.opt_minimize = QAction("Minimize while playing or recording", self, checkable=True)
        self.opt_awake = QAction("Keep the screen awake while playing", self, checkable=True)
        self.opt_ontop = QAction("Keep this window on top", self, checkable=True)
        self.opt_ontop.toggled.connect(self._set_on_top)
        for a in (self.opt_countdown, self.opt_minimize, self.opt_awake, self.opt_ontop):
            o.addAction(a)

        h = mb.addMenu("&Help")
        h.addAction("How &input works on this system", self.show_input_help)
        h.addAction("&About Macro Tool", self.show_about)

    # ------------------------------------------------------------ settings
    def _load_settings(self):
        st = self.settings
        hk = {a: st.value(f"hotkeys/{a}", d) for a, d in DEFAULT_HOTKEYS.items()}
        try:
            self.hub.set_hotkeys(hk)
        except keys.KeyNameError:
            self.hub.set_hotkeys(dict(DEFAULT_HOTKEYS))
        self.opt_countdown.setChecked(st.value("options/countdown", True, bool))
        self.opt_minimize.setChecked(st.value("options/minimize", False, bool))
        self.opt_awake.setChecked(st.value("options/keep_awake", True, bool))
        self.opt_ontop.setChecked(st.value("options/on_top", False, bool))
        self.m_moves.setChecked(st.value("options/record_moves", True, bool))
        interval = st.value("clicker/interval_ms", 100, int)
        self._set_interval(interval)
        self.ac_jitter.setValue(st.value("clicker/jitter_ms", 0, int))
        self.ac_button.setCurrentIndex(max(0, self.ac_button.findData(st.value("clicker/button", "left"))))
        self.ac_clicks.setCurrentIndex(st.value("clicker/clicks", 1, int) - 1)
        if st.value("clicker/fixed", False, bool):
            self.ac_point.setPoint((st.value("clicker/x", 0, int), st.value("clicker/y", 0, int)))
        if st.value("clicker/forever", True, bool):
            self.ac_forever.setChecked(True)
        else:
            self.ac_times_rb.setChecked(True)
        self.ac_times.setValue(st.value("clicker/times", 100, int))
        self.tabs.setCurrentIndex(st.value("ui/tab", 0, int))
        geo = st.value("ui/geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        else:
            self.resize(860, 600)
        last = st.value("file/current", "")
        session = config_dir() / "session.json"
        loaded = False
        if session.exists():
            try:
                self._set_macro(model.load(session), None)
                self.file = Path(last) if last and Path(last).exists() else None
                self._set_dirty(st.value("file/dirty", False, bool))
                loaded = True
            except (OSError, MacroError):
                pass
        if not loaded:
            self._set_macro(Macro(), None)
        self._update_rate()
        self._update_recent()
        self._update_title()

    def _save_settings(self):
        st = self.settings
        for a, c in self.hub.hotkeys.items():
            st.setValue(f"hotkeys/{a}", c)
        st.setValue("options/countdown", self.opt_countdown.isChecked())
        st.setValue("options/minimize", self.opt_minimize.isChecked())
        st.setValue("options/keep_awake", self.opt_awake.isChecked())
        st.setValue("options/on_top", self.opt_ontop.isChecked())
        st.setValue("options/record_moves", self.m_moves.isChecked())
        st.setValue("clicker/interval_ms", self._interval())
        st.setValue("clicker/jitter_ms", self.ac_jitter.value())
        st.setValue("clicker/button", self.ac_button.currentData())
        st.setValue("clicker/clicks", self.ac_clicks.currentIndex() + 1)
        p = self.ac_point.point()
        st.setValue("clicker/fixed", p is not None)
        if p is not None:
            st.setValue("clicker/x", p[0])
            st.setValue("clicker/y", p[1])
        st.setValue("clicker/forever", self.ac_forever.isChecked())
        st.setValue("clicker/times", self.ac_times.value())
        st.setValue("ui/tab", self.tabs.currentIndex())
        st.setValue("ui/geometry", self.saveGeometry())
        st.setValue("file/current", str(self.file) if self.file else "")
        st.setValue("file/dirty", self.dirty)
        st.sync()

    def _save_session(self):
        try:
            model.save(self._macro_from_ui(), config_dir() / "session.json")
        except OSError:
            pass

    # ------------------------------------------------------------ hub
    def _start_hub(self):
        ok = self.hub.start()
        parts = [f"Input: {self.backend.description}"]
        if self.pointer_note:
            parts.append(self.pointer_note)
        if not ok:
            parts.append("hotkeys off")
        self.backend_label.setText("  ·  ".join(parts))
        if not ok or self.hub.error:
            QTimer.singleShot(300, lambda: QMessageBox.warning(
                self, "Hotkeys and recording are off",
                (self.hub.error or "Global input can't be read on this system.") +
                "\n\nPlaying still works. Use the Stop button to stop."))

    def _on_hotkey(self, action: str):
        if QApplication.activeModalWidget() is not None and not self.is_busy:
            return
        if action == "play":
            if self.is_busy:
                self.stop_all()
            else:
                self.start_playing(from_button=False)
        elif action == "stop":
            self.stop_all()
        elif action == "record":
            self.toggle_recording(from_button=False)

    # ------------------------------------------------------------ state
    @property
    def playing(self) -> bool:
        return self.player is not None

    @property
    def is_busy(self) -> bool:
        return self.playing or self.hub.recording or self._countdown.isActive()

    def _hk(self, action: str) -> str:
        c = self.hub.hotkeys.get(action)
        return f"   {keys.format_combo(c)}" if c else ""

    def _sync_buttons(self):
        busy = self.is_busy
        rec = self.hub.recording
        self.play_btn.setText(("Start" if self.tabs.currentIndex() == 0 else "Play") + self._hk("play"))
        self.stop_btn.setText("Stop" + self._hk("stop"))
        self.record_btn.setText(("Stop recording" if rec else "Record") + self._hk("record"))
        self.play_btn.setEnabled(not busy)
        self.stop_btn.setEnabled(busy)
        self.record_btn.setEnabled((not self.playing and not self._countdown.isActive()) and self.hub.active)
        if not self.hub.active:
            self.record_btn.setToolTip("Recording needs global input access. See Help > How input works.")
        rows = self._selected_rows()
        editing = not busy
        has = bool(self.steps_model.steps)
        self.table.setVisible(has)
        self.empty_label.setVisible(not has)
        self.add_btn.setEnabled(editing)
        self.edit_btn.setEnabled(editing and len(rows) == 1)
        self.act_edit.setEnabled(editing and len(rows) == 1)
        for b in (self.dup_btn, self.del_btn, self.act_dup, self.act_del):
            b.setEnabled(editing and bool(rows))
        self.up_btn.setEnabled(editing and bool(rows) and rows[0] > 0)
        self.down_btn.setEnabled(editing and bool(rows) and rows[-1] < len(self.steps_model.steps) - 1)
        self.points_btn.setEnabled(editing and any(s.has_point for s in self.steps_model.steps))
        self.tabs.tabBar().setEnabled(not busy)
        self.tabs.widget(0).setEnabled(not busy)
        self._refresh_status()

    def _refresh_status(self):
        if self._countdown.isActive():
            text = f"Starting in {self._countdown_left}…"
        elif self.hub.recording:
            text = f"Recording  ·  {fmt_elapsed(time.monotonic() - self._record_started)}"
        elif self.player is not None:
            p = self._last_progress
            elapsed = fmt_elapsed(time.monotonic() - self.player.started) if self.player.started else "0:00"
            if self.play_mode == "clicker":
                n = p.actions if p else 0
                text = f"Clicking  ·  {n:,} clicks  ·  {elapsed}"
            else:
                loop = p.loop if p else 1
                total = "" if self.player.macro.repeat == 0 else f" of {self.player.macro.repeat:,}"
                text = f"Playing  ·  loop {loop:,}{total}  ·  {elapsed}"
        else:
            text = "Ready"
        self.state_label.setText(text)

    def _on_clean_changed(self, clean: bool):
        self._set_dirty(not clean)

    def _set_dirty(self, dirty: bool):
        self.dirty = dirty
        self._update_title()

    def _update_title(self):
        name = self.file.name if self.file else "Untitled"
        self.setWindowTitle(f"{'*' if self.dirty else ''}{name} - Macro Tool")

    # ------------------------------------------------------------ auto clicker
    def _interval(self) -> int:
        return ((self.ac_h.value() * 60 + self.ac_m.value()) * 60 + self.ac_s.value()) * 1000 + self.ac_ms.value()

    def _set_interval(self, ms: int):
        ms = max(0, int(ms))
        self.ac_h.setValue(ms // 3_600_000)
        self.ac_m.setValue(ms // 60_000 % 60)
        self.ac_s.setValue(ms // 1000 % 60)
        self.ac_ms.setValue(ms % 1000)

    def _update_rate(self):
        ms = self._interval()
        if ms == 0:
            text = "As fast as possible, about 1,000 clicks a second. Some apps drop clicks this fast."
        elif ms < 1000:
            text = f"About {1000 / ms:,.1f} clicks a second"
        else:
            per_min = 60_000 / ms
            text = f"About {per_min:,.1f} clicks a minute" if per_min >= 1 else f"One click every {fmt_ms(ms)}"
        self.ac_rate.setText(text)

    def _clicker_macro(self) -> Macro:
        p = self.ac_point.point()
        step = Step("click", button=self.ac_button.currentData(), clicks=self.ac_clicks.currentIndex() + 1,
                    delay_ms=self._interval())
        if p is not None:
            step.x, step.y = p
        return Macro(steps=[step], repeat=0 if self.ac_forever.isChecked() else self.ac_times.value(),
                     jitter_ms=self.ac_jitter.value())

    def _pick_clicker_point(self):
        p = self.ac_point.point()
        pts = self.pick("single", [p] if p else None)
        if pts:
            self.ac_point.setPoint(pts[0])

    # ------------------------------------------------------------ picking
    def pick(self, mode: str, points=None, labels=None, extra_hide=None):
        hide = [self] + [w for w in (extra_hide or []) if w is not None]
        self.hub.suspended += 1
        try:
            return picker.pick(mode, points, self.backend.native_pixels, labels, hide=hide)
        finally:
            self.hub.suspended -= 1

    def _dialog_pick(self, dialog, points):
        return self.pick("single", points, extra_hide=[dialog])

    # ------------------------------------------------------------ macro editing
    def _macro_from_ui(self) -> Macro:
        m = Macro(steps=[s.copy() for s in self.steps_model.steps])
        m.repeat = 0 if self.m_forever.isChecked() else self.m_times.value()
        m.stop_after_s = self.m_limit.value() / 1000.0
        m.speed = self.m_speed.value()
        m.jitter_ms = self.m_jitter_ms.value()
        m.jitter_px = self.m_jitter_px.value()
        return m

    def _set_macro(self, m: Macro, path: Path | None):
        self.undo.clear()
        self._apply_steps([s.copy() for s in m.steps], [])
        widgets = (self.m_times, self.m_speed, self.m_jitter_ms, self.m_jitter_px, self.m_forever, self.m_times_rb, self.m_limit)
        for w in widgets:
            w.blockSignals(True)
        if m.repeat == 0:
            self.m_forever.setChecked(True)
        else:
            self.m_times_rb.setChecked(True)
            self.m_times.setValue(m.repeat)
        self.m_limit.setValue(int(m.stop_after_s * 1000))
        self.m_speed.setValue(m.speed)
        self.m_jitter_ms.setValue(m.jitter_ms)
        self.m_jitter_px.setValue(m.jitter_px)
        for w in widgets:
            w.blockSignals(False)
        self.file = path
        self._set_dirty(False)

    def _apply_steps(self, steps: list[Step], select: list[int]):
        self.steps_model.set_steps(steps)
        sel = self.table.selectionModel()
        sel.clearSelection()
        for r in select:
            if 0 <= r < len(steps):
                sel.select(self.steps_model.index(r, 0),
                           sel.SelectionFlag.Select | sel.SelectionFlag.Rows)
        if select:
            self.table.scrollTo(self.steps_model.index(select[-1], 0))
            self.table.setCurrentIndex(self.steps_model.index(select[-1], 0))
        self._sync_buttons()
        self._autosave.start()

    def _change(self, new_steps: list[Step], text: str, select: list[int]):
        self.undo.push(Snapshot(self, self.steps_model.steps, new_steps, text, select))

    def _options_changed(self, *_):
        self.dirty = True
        self._update_title()
        self._autosave.start()

    def _selected_rows(self) -> list[int]:
        return sorted({i.row() for i in self.table.selectionModel().selectedRows()})

    def _insert_at(self) -> int:
        rows = self._selected_rows()
        return rows[-1] + 1 if rows else len(self.steps_model.steps)

    def add_step(self, step: Step, edit=True, pick_first=False):
        if pick_first:
            pts = self.pick("single")
            if not pts:
                return
            step.x, step.y = pts[0]
        if edit:
            dlg = StepDialog(step, pick=self._dialog_pick, parent=self, full_unicode=self.backend.full_unicode)
            if step.kind == "text":
                dlg.text.selectAll()
            if not dlg.exec():
                return
            step = dlg.step
        at = self._insert_at()
        steps = self.steps_model.steps[:at] + [step] + self.steps_model.steps[at:]
        self._change(steps, f"Add {model.KINDS[step.kind].lower()}", [at])

    def add_click(self, multi: bool):
        pts = self.pick("multi" if multi else "single")
        if not pts:
            return
        new = [Step("click", x=x, y=y, delay_ms=300) for x, y in pts]
        at = self._insert_at()
        steps = self.steps_model.steps[:at] + new + self.steps_model.steps[at:]
        self._change(steps, "Add clicks" if len(new) > 1 else "Add click", list(range(at, at + len(new))))

    def edit_step(self):
        rows = self._selected_rows()
        if len(rows) != 1 or self.is_busy:
            return
        r = rows[0]
        dlg = StepDialog(self.steps_model.steps[r], pick=self._dialog_pick, parent=self,
                         full_unicode=self.backend.full_unicode)
        if dlg.exec() and dlg.step != self.steps_model.steps[r]:
            steps = list(self.steps_model.steps)
            steps[r] = dlg.step
            self._change(steps, "Edit step", [r])

    def duplicate_steps(self):
        rows = self._selected_rows()
        if not rows or self.is_busy:
            return
        steps = list(self.steps_model.steps)
        copies = [steps[r].copy() for r in rows]
        at = rows[-1] + 1
        steps[at:at] = copies
        self._change(steps, "Duplicate", list(range(at, at + len(copies))))

    def remove_steps(self):
        rows = self._selected_rows()
        if not rows or self.is_busy:
            return
        steps = [s for i, s in enumerate(self.steps_model.steps) if i not in set(rows)]
        self._change(steps, "Remove", [min(rows[0], len(steps) - 1)] if steps else [])

    def move_steps(self, delta: int):
        rows = self._selected_rows()
        if not rows or self.is_busy:
            return
        target = rows[0] + delta if delta < 0 else rows[-1] + 2
        self._drop_rows(rows, target)

    def _drop_rows(self, rows: list[int], dest: int):
        if self.is_busy:
            return
        steps = list(self.steps_model.steps)
        moving = [steps[r] for r in rows]
        dest -= sum(1 for r in rows if r < dest)
        rest = [s for i, s in enumerate(steps) if i not in set(rows)]
        dest = max(0, min(dest, len(rest)))
        new = rest[:dest] + moving + rest[dest:]
        if [id(s) for s in new] == [id(s) for s in steps]:
            return
        self._change(new, "Move", list(range(dest, dest + len(moving))))

    def _toggle_step(self, row: int, on: bool):
        steps = [s.copy() for s in self.steps_model.steps]
        steps[row].enabled = on
        self._change(steps, "Enable step" if on else "Skip step", self._selected_rows())

    def edit_points(self):
        idx = [i for i, s in enumerate(self.steps_model.steps) if s.has_point]
        if not idx:
            return
        pts = [(self.steps_model.steps[i].x, self.steps_model.steps[i].y) for i in idx]
        labels = [str(i + 1) for i in idx]
        result = self.pick("edit", pts, labels)
        if not result or result == pts:
            return
        steps = [s.copy() for s in self.steps_model.steps]
        for i, (x, y) in zip(idx, result):
            steps[i].x, steps[i].y = x, y
        self._change(steps, "Move points", [])

    def _table_menu(self, pos):
        if self.is_busy:
            return
        menu = QMenu(self)
        rows = self._selected_rows()
        if len(rows) == 1:
            menu.addAction("Edit…", self.edit_step)
        if rows:
            menu.addAction("Duplicate", self.duplicate_steps)
            menu.addAction("Remove", self.remove_steps)
            menu.addSeparator()
            menu.addAction("Skip these steps", lambda: self._set_enabled(rows, False))
            menu.addAction("Don't skip these steps", lambda: self._set_enabled(rows, True))
            menu.addSeparator()
        menu.addMenu(self.add_btn.menu())
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _set_enabled(self, rows, on):
        steps = [s.copy() for s in self.steps_model.steps]
        for r in rows:
            steps[r].enabled = on
        self._change(steps, "Skip steps" if not on else "Enable steps", rows)

    # ------------------------------------------------------------ playing
    def start_playing(self, from_button: bool):
        if self.is_busy:
            return
        self.play_mode = "clicker" if self.tabs.currentIndex() == 0 else "macro"
        macro = self._clicker_macro() if self.play_mode == "clicker" else self._macro_from_ui()
        if not [s for s in macro.steps if s.enabled]:
            QMessageBox.information(self, "Nothing to play", "Add a step or record one first.")
            return
        bad = [s for s in macro.steps if s.has_point and not self._on_screen(s)]
        if bad:
            r = QMessageBox.warning(
                self, "Point off screen",
                f"{len(bad)} point{'s are' if len(bad) > 1 else ' is'} outside every screen, "
                "probably from a different monitor setup. Play anyway?",
                QMessageBox.Yes | QMessageBox.No)
            if r != QMessageBox.Yes:
                return
        if macro.repeat == 0 and macro.stop_after_s == 0 and not self.hub.active:
            r = QMessageBox.warning(
                self, "No stop hotkey",
                "Hotkeys aren't working on this system, so this can only be stopped with the Stop button. Start anyway?",
                QMessageBox.Yes | QMessageBox.No)
            if r != QMessageBox.Yes:
                return
        self._pending = macro
        if from_button and self.opt_countdown.isChecked():
            self._countdown_left = 3
            self._countdown.start()
            self._sync_buttons()
            if self.opt_minimize.isChecked():
                self.showMinimized()
            return
        self._launch(macro)

    def _on_screen(self, s: Step) -> bool:
        from . import coords
        return coords.on_any_screen(s.x, s.y, self.backend.native_pixels)

    def _tick_countdown(self):
        self._countdown_left -= 1
        if self._countdown_left <= 0:
            self._countdown.stop()
            self._launch(self._pending)
        else:
            self._refresh_status()

    def _launch(self, macro: Macro):
        self._last_progress = None
        self.player = Player(self.backend, macro,
                             on_progress=self.progress_signal.emit,
                             on_finish=self.finish_signal.emit,
                             progress_interval_s=0.1)
        if self.opt_awake.isChecked():
            self.keepawake.start()
        if self.opt_minimize.isChecked() and not self.isMinimized():
            self.showMinimized()
        self.player.start()
        self._status_timer.start()
        self._sync_buttons()

    def _on_progress(self, p: Progress):
        self._last_progress = p
        if self.play_mode == "macro":
            # step index counts only enabled steps; map back to the table row
            enabled = [i for i, s in enumerate(self.steps_model.steps) if s.enabled]
            if 0 <= p.step < len(enabled):
                self.steps_model.set_current(enabled[p.step])

    def _on_finish(self, r: Result):
        self.player = None
        self._status_timer.stop()
        self.keepawake.stop()
        self.steps_model.set_current(-1)
        if self.isMinimized() and self.opt_minimize.isChecked():
            self.showNormal()
        what = "clicks" if self.play_mode == "clicker" else ("loops" if r.loops != 1 else "loop")
        count = r.actions if self.play_mode == "clicker" else r.loops
        if r.reason == "error":
            self.statusBar().showMessage(f"Stopped by an error after {fmt_elapsed(r.seconds)}", 15000)
            QMessageBox.warning(self, "Playback stopped", r.error)
        else:
            verb = "Finished" if r.reason == "done" else "Stopped"
            self.statusBar().showMessage(f"{verb}: {count:,} {what} in {fmt_elapsed(r.seconds)}", 15000)
        self._sync_buttons()

    def stop_all(self):
        if self._countdown.isActive():
            self._countdown.stop()
            if self.isMinimized() and self.opt_minimize.isChecked():
                self.showNormal()
            self._sync_buttons()
            return
        if self.hub.recording:
            self.toggle_recording(from_button=self.sender() is self.stop_btn)
            return
        if self.player is not None:
            self.player.stop(wait_s=0)

    # ------------------------------------------------------------ recording
    def toggle_recording(self, from_button: bool):
        if self.hub.recording:
            steps, full = self.hub.stop_recording(from_button=from_button)
            self._status_timer.stop()
            if self.isMinimized() and self.opt_minimize.isChecked():
                self.showNormal()
            self._finish_recording(steps, full)
            return
        if self.playing or self._countdown.isActive() or not self.hub.active:
            return
        self._record_mode = "replace"
        if self.steps_model.steps:
            box = QMessageBox(QMessageBox.Question, "Record",
                              "This macro already has steps. Add the recording to the end, or replace them?",
                              parent=self)
            add = box.addButton("Add to the end", QMessageBox.AcceptRole)
            rep = box.addButton("Replace", QMessageBox.DestructiveRole)
            box.addButton(QMessageBox.Cancel)
            box.setDefaultButton(add)
            if from_button or self.isActiveWindow():
                box.exec()
                if box.clickedButton() is rep:
                    self._record_mode = "replace"
                elif box.clickedButton() is add:
                    self._record_mode = "append"
                else:
                    return
            else:
                self._record_mode = "append"
        self.tabs.setCurrentIndex(1)
        self.hub.start_recording(record_moves=self.m_moves.isChecked())
        self._record_started = time.monotonic()
        self._status_timer.start()
        if self.opt_minimize.isChecked():
            self.showMinimized()
        if self.backend.name == "uinput" and not self.backend.can_read_pointer and self.m_moves.isChecked():
            self.statusBar().showMessage(
                "This desktop doesn't share the pointer position, so clicks are recorded without a place.", 10000)
        self._sync_buttons()

    def _finish_recording(self, steps: list[Step], full: bool):
        if full:
            QMessageBox.information(self, "Recording was cut short",
                                    "The recording reached 500,000 events and stopped taking new ones.")
        if not steps:
            self.statusBar().showMessage("Nothing was recorded.", 8000)
            self._sync_buttons()
            return
        if self._record_mode == "append":
            base = list(self.steps_model.steps)
            new = base + steps
            sel = list(range(len(base), len(new)))
        else:
            new = steps
            sel = []
        self._change(new, "Record", sel)
        self.statusBar().showMessage(f"Recorded {len(steps):,} steps.", 8000)
        self._sync_buttons()

    # ------------------------------------------------------------ files
    def _confirm_discard(self) -> bool:
        if not self.dirty or not self.steps_model.steps:
            return True
        r = QMessageBox.question(self, "Unsaved changes", "Save the changes to this macro first?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save()
        return r == QMessageBox.Discard

    def new_file(self):
        if self.is_busy or not self._confirm_discard():
            return
        self._set_macro(Macro(), None)
        self.tabs.setCurrentIndex(1)

    def open_dialog(self):
        if self.is_busy or not self._confirm_discard():
            return
        start = str(self.file.parent if self.file else Path.home())
        path, _ = QFileDialog.getOpenFileName(self, "Open a macro", start, FILE_FILTER)
        if path:
            self.open_path(Path(path))

    def open_path(self, path: Path):
        try:
            m = model.load(path)
        except OSError as e:
            QMessageBox.warning(self, "Can't open this file", f"{path.name}: {e.strerror or e}.")
            return False
        except (MacroError, ValueError) as e:
            QMessageBox.warning(self, "Can't open this file", f"{path.name}: {e}.")
            return False
        self._set_macro(m, path)
        self.tabs.setCurrentIndex(1)
        self._add_recent(path)
        self.statusBar().showMessage(f"Opened {path.name}: {len(m.steps)} steps", 6000)
        return True

    def save(self) -> bool:
        if self.file is None:
            return self.save_as()
        return self._write(self.file)

    def save_as(self) -> bool:
        start = str(self.file if self.file else Path.home() / "macro.json")
        path, _ = QFileDialog.getSaveFileName(self, "Save the macro", start, FILE_FILTER)
        if not path:
            return False
        p = Path(path)
        if not p.suffix:
            p = p.with_suffix(".json")
        return self._write(p)

    def _write(self, path: Path) -> bool:
        try:
            model.save(self._macro_from_ui(), path)
        except OSError as e:
            QMessageBox.warning(self, "Can't save", f"{path}: {e.strerror or e}")
            return False
        self.file = path
        self.undo.setClean()
        self._set_dirty(False)
        self._add_recent(path)
        self.statusBar().showMessage(f"Saved {path.name}", 5000)
        return True

    def _add_recent(self, path: Path):
        recent = [p for p in self.settings.value("file/recent", [], list) if p != str(path)]
        recent.insert(0, str(path))
        self.settings.setValue("file/recent", recent[:8])
        self._update_recent()

    def _update_recent(self):
        self.recent_menu.clear()
        recent = [p for p in self.settings.value("file/recent", [], list) if p]
        for p in recent:
            self.recent_menu.addAction(p, lambda _=False, p=p: (self._confirm_discard() and self.open_path(Path(p))))
        self.recent_menu.setEnabled(bool(recent))

    # ------------------------------------------------------------ options / help
    def edit_hotkeys(self):
        self.hub.suspended += 1
        try:
            dlg = HotkeysDialog(self.hub.hotkeys, HOTKEY_LABELS, self)
            if dlg.exec():
                self.hub.set_hotkeys(dlg.values())
                self._save_settings()
                self._sync_buttons()
        finally:
            self.hub.suspended -= 1

    def _set_on_top(self, on: bool):
        visible = self.isVisible()
        self.setWindowFlag(Qt.WindowStaysOnTopHint, on)
        if visible:
            self.show()

    def show_input_help(self):
        from .backends.uinput_backend import SETUP_HELP
        st = session_type()
        if self.backend.name == "uinput":
            pos = ("This desktop reports the pointer position, so recordings keep it."
                   if self.backend.can_read_pointer else
                   "This desktop doesn't tell apps where the pointer is. Clicks you add with Pick work "
                   "normally, but recorded clicks happen wherever the pointer is when they play.")
            text = ("You're on Wayland. Macro Tool sends input through a virtual keyboard and mouse "
                    "(Linux uinput) and reads hotkeys straight from your input devices.\n\n" + pos +
                    "\n\nIf hotkeys or playing don't work:\n\n" + SETUP_HELP)
        elif st == "macos":
            text = ("macOS asks for two permissions the first time: Accessibility (to click and type) and "
                    "Input Monitoring (for hotkeys and recording). Turn on Macro Tool in System Settings > "
                    "Privacy & Security for both, then quit and reopen it.\n\nScreen Recording is optional: "
                    "with it, the magnifier shows the real pixels when you pick a point.")
        elif st == "windows":
            text = ("Macro Tool works with normal apps right away. To click in an app that runs as "
                    "administrator, run Macro Tool as administrator too.")
        else:
            text = "You're on X11. Clicking, typing, hotkeys and recording all work without setup."
        QMessageBox.information(self, "How input works here", text)

    def show_about(self):
        QMessageBox.about(self, "About Macro Tool",
                          f"<b>Macro Tool {__version__}</b><p>Record and replay mouse and keyboard "
                          "macros, or run an auto clicker.</p><p>MIT license. "
                          "<a href='https://github.com/YatoVoid/Macro-Tool'>github.com/YatoVoid/Macro-Tool</a></p>")

    # ------------------------------------------------------------ shutdown
    def closeEvent(self, ev: QCloseEvent):
        if self.hub.recording:
            self.hub.stop_recording(from_button=True)
        if self.player is not None:
            self.player.stop(wait_s=2)
            self.player = None
        self._countdown.stop()
        self.keepawake.stop()
        if self.dirty and self.steps_model.steps and self.file is not None:
            if not self._confirm_discard():
                ev.ignore()
                return
        self._save_session()
        self._save_settings()
        self.hub.stop()
        self.backend.release_all()
        ev.accept()
