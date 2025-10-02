#!/usr/bin/env python3
"""
macro_tool.py

Run: python3 macro_tool.py

Dependencies:
    pip install PySide6 pyautogui pynput
"""

import sys
import json
import threading
import time
import os
from pathlib import Path
from dataclasses import dataclass, asdict, field
from typing import List, Optional

from PySide6.QtCore import Qt, QTimer, QSize, QEvent
from PySide6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QRadioButton, QGroupBox, QStackedWidget,
    QLineEdit, QComboBox, QSpinBox, QFileDialog, QListWidget,
    QFormLayout, QDialog, QDialogButtonBox, QMessageBox, QScrollArea
)

import pyautogui
from pynput import mouse as pynput_mouse
from pynput import keyboard as pynput_keyboard
from pynput.keyboard import Key, Controller as KeyboardController
from pynput.mouse import Controller as MouseController

pyautogui.FAILSAFE = False  # optional: disable moving to corner to stop

# ---------------------------
# Data classes for saving
# ---------------------------
@dataclass
class ClickAction:
    x: int
    y: int
    delay_ms: int = 500
    unit: str = 'ms'  # 'ms' or 's'
    action_type: str = 'left'  # 'left'|'right'|'middle'|'key'
    key_char: Optional[str] = None

@dataclass
class Config:
    start_hotkey: str = '<f9>'
    stop_hotkey: str = '<f10>'
    items: List[ClickAction] = field(default_factory=list)
    last_saved: Optional[str] = None

# ---------------------------
# Helpers
# ---------------------------
mouse_ctrl = MouseController()
kb_ctrl = KeyboardController()

def parse_hotkey_string(s: str) -> str:
    s = s.strip()
    if s.startswith('<') and s.endswith('>'):
        return s.lower()
    special = {
        'enter': '<enter>',
        'f1': '<f1>', 'f2': '<f2>', 'f3': '<f3>', 'f4': '<f4>',
        'f5': '<f5>', 'f6': '<f6>', 'f7': '<f7>', 'f8': '<f8>',
        'f9': '<f9>', 'f10': '<f10>', 'f11': '<f11>', 'f12': '<f12>',
        'space': '<space>',
        'esc': '<esc>',
        'escape': '<esc>',
    }
    key = s.lower()
    if key in special:
        return special[key]
    if len(key) == 1:
        return key
    return f'<{key}>'

def click_at(action: ClickAction):
    if action.action_type == 'key' and action.key_char:
        pyautogui.press(action.key_char)
        return
    button = {'left': 'left', 'right': 'right', 'middle': 'middle'}.get(action.action_type, 'left')
    pyautogui.click(action.x, action.y, button=button)

# ---------------------------
# Custom event to safely call functions on main thread
# ---------------------------
class _FuncEvent(QEvent):
    def __init__(self, callback):
        super().__init__(QEvent.User)
        self.callback = callback

# ---------------------------
# Settings Dialog
# ---------------------------
class SettingsDialog(QDialog):
    def __init__(self, parent=None, config: Config = None):
        super().__init__(parent)
        self.setWindowTitle("Settings — Keybinds")
        self.setMinimumSize(360, 140)
        self.config = config or Config()
        layout = QFormLayout(self)

        self.start_edit = QLineEdit(self.config.start_hotkey)
        self.stop_edit = QLineEdit(self.config.stop_hotkey)

        layout.addRow("Start hotkey (example: F9 or <f9>):", self.start_edit)
        layout.addRow("Stop hotkey (example: F10 or <f10>):", self.stop_edit)

        info = QLabel("Use single key or function keys. Examples: F9, enter, space, a")
        info.setWordWrap(True)
        layout.addRow(info)

        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addRow(btns)

    def accept(self) -> None:
        self.config.start_hotkey = parse_hotkey_string(self.start_edit.text())
        self.config.stop_hotkey = parse_hotkey_string(self.stop_edit.text())
        super().accept()

# ---------------------------
# MultiClick item widget
# ---------------------------
class MultiItemWidget(QWidget):
    def __init__(self, action: ClickAction, parent=None, remove_callback=None):
        super().__init__(parent)
        self.action = action
        self.remove_callback = remove_callback

        layout = QHBoxLayout(self)
        self.pos_label = QLineEdit(f"{action.x}, {action.y}")
        self.pos_label.setReadOnly(True)

        self.delay_spin = QSpinBox()
        self.delay_spin.setMaximum(60_000_000)
        self.delay_spin.setValue(action.delay_ms)

        self.unit_combo = QComboBox()
        self.unit_combo.addItems(['ms', 's'])
        self.unit_combo.setCurrentText(action.unit)

        self.type_combo = QComboBox()
        self.type_combo.addItems(['left', 'right', 'middle', 'key'])
        self.type_combo.setCurrentText(action.action_type)

        self.key_edit = QLineEdit(action.key_char or '')
        self.key_edit.setMaximumWidth(50)
        self.key_edit.setPlaceholderText('k')

        self.set_pos_btn = QPushButton("Set pos (Enter)")
        self.remove_btn = QPushButton("Remove")

        layout.addWidget(self.pos_label)
        layout.addWidget(QLabel("Delay"))
        layout.addWidget(self.delay_spin)
        layout.addWidget(self.unit_combo)
        layout.addWidget(self.type_combo)
        layout.addWidget(self.key_edit)
        layout.addWidget(self.set_pos_btn)
        layout.addWidget(self.remove_btn)

        self.set_pos_btn.clicked.connect(self.start_set_pos)
        self.remove_btn.clicked.connect(self._on_remove)
        self.type_combo.currentTextChanged.connect(self._on_type_change)
        self.delay_spin.valueChanged.connect(self._on_delay_change)
        self.unit_combo.currentTextChanged.connect(self._on_unit_change)
        self.key_edit.textChanged.connect(self._on_key_change)

        self._update_key_visibility()

    def _on_type_change(self, t):
        self.action.action_type = t
        self._update_key_visibility()

    def _on_delay_change(self, val):
        self.action.delay_ms = val

    def _on_unit_change(self, unit):
        self.action.unit = unit

    def _on_key_change(self, text):
        self.action.key_char = text or None

    def _update_key_visibility(self):
        self.key_edit.setVisible(self.action.action_type == 'key')

    def _on_remove(self):
        if self.remove_callback:
            self.remove_callback(self)

    def start_set_pos(self):
        QMessageBox.information(self, "Set Position",
                                "Move your mouse to desired position and press Enter.",
                                QMessageBox.Ok)
        captured = {'pos': None}
        def on_press(key):
            if key == Key.enter:
                captured['pos'] = pyautogui.position()
                return False
        with pynput_keyboard.Listener(on_press=on_press) as listener:
            listener.join()
        if captured['pos']:
            px, py = captured['pos']
            self.action.x = px
            self.action.y = py
            self.pos_label.setText(f"{px}, {py}")

# ---------------------------
# Main Window
# ---------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Macro Tool")
        self.resize(700, 520)
        self.config = Config()
        self.exec_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.hotkey_listener = None

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        # Mode selection
        mode_group = QGroupBox("Mode")
        mg_layout = QHBoxLayout(mode_group)
        self.rb_single = QRadioButton("Single Click")
        self.rb_multi = QRadioButton("Multi Click")
        self.rb_record = QRadioButton("Record")
        self.rb_single.setChecked(True)
        mg_layout.addWidget(self.rb_single)
        mg_layout.addWidget(self.rb_multi)
        mg_layout.addWidget(self.rb_record)
        main_layout.addWidget(mode_group)

        # Stack pages
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_single_page())
        self.stack.addWidget(self._build_multi_page())
        self.stack.addWidget(self._build_record_page())
        main_layout.addWidget(self.stack)

        # Bottom buttons
        bottom = QHBoxLayout()
        self.start_btn = QPushButton("Start (F9)")
        self.stop_btn = QPushButton("Stop (F10)")
        self.settings_btn = QPushButton("Settings ⚙")
        self.save_btn = QPushButton("Save Config")
        self.load_btn = QPushButton("Load Config")
        bottom.addWidget(self.start_btn)
        bottom.addWidget(self.stop_btn)
        bottom.addWidget(self.settings_btn)
        bottom.addWidget(self.save_btn)
        bottom.addWidget(self.load_btn)
        main_layout.addLayout(bottom)

        # Connect signals
        self.rb_single.toggled.connect(lambda val: self._on_mode_changed(0 if val else None))
        self.rb_multi.toggled.connect(lambda val: self._on_mode_changed(1 if val else None))
        self.rb_record.toggled.connect(lambda val: self._on_mode_changed(2 if val else None))
        self.start_btn.clicked.connect(self.start_execution)
        self.stop_btn.clicked.connect(self.stop_execution)
        self.settings_btn.clicked.connect(self.open_settings)
        self.save_btn.clicked.connect(self.save_config_dialog)
        self.load_btn.clicked.connect(self.load_config_dialog)

        # Mouse timer
        self._mouse_timer = QTimer(self)
        self._mouse_timer.timeout.connect(self._update_mouse_label)
        self._mouse_timer.start(80)

        # Install hotkeys
        self._install_hotkeys()

    # -----------------------
    # Stack pages
    # -----------------------
    def _build_single_page(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        self.mouse_pos_label = QLabel("Mouse: 0, 0")
        self.mouse_pos_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.mouse_pos_label)

        form = QHBoxLayout()
        self.single_btn_setpos = QPushButton("Set Position (Enter)")
        self.single_btn_setpos.clicked.connect(self.single_set_pos)
        form.addWidget(self.single_btn_setpos)

        self.single_click_type = QComboBox()
        self.single_click_type.addItems(['left', 'right', 'middle', 'key'])
        form.addWidget(QLabel("Click:"))
        form.addWidget(self.single_click_type)

        self.single_key_edit = QLineEdit()
        self.single_key_edit.setMaximumWidth(60)
        self.single_key_edit.setPlaceholderText('k')
        form.addWidget(self.single_key_edit)

        form.addWidget(QLabel("Delay (ms):"))
        self.single_delay = QSpinBox()
        self.single_delay.setMaximum(60_000_000)
        self.single_delay.setValue(500)
        form.addWidget(self.single_delay)

        layout.addLayout(form)
        layout.addStretch()
        return w

    def _build_multi_page(self):
        w = QWidget()
        v = QVBoxLayout(w)
        top = QHBoxLayout()
        self.add_item_btn = QPushButton("+ Add Action")
        self.add_item_btn.clicked.connect(self._add_multi_item)
        top.addWidget(self.add_item_btn)
        v.addLayout(top)

        self.multi_area = QScrollArea()
        self.multi_area.setWidgetResizable(True)
        self.multi_container = QWidget()
        self.multi_layout = QVBoxLayout(self.multi_container)
        self.multi_layout.addStretch()
        self.multi_area.setWidget(self.multi_container)
        v.addWidget(self.multi_area)
        return w

    def _build_record_page(self):
        w = QWidget()
        v = QVBoxLayout(w)
        instructions = QLabel("Press Record to begin. Press Enter (or stop hotkey) to stop recording.\nRecorded events will be replayed in order.")
        instructions.setWordWrap(True)
        v.addWidget(instructions)
        self.record_btn = QPushButton("Record")
        self.record_btn.clicked.connect(self.toggle_recording)
        v.addWidget(self.record_btn)
        self.record_list = QListWidget()
        v.addWidget(self.record_list)

        return w

    # -----------------------
    # Mode change
    # -----------------------
    def _on_mode_changed(self, page_index):
        if page_index is None:
            page_index = 0 if self.rb_single.isChecked() else 1 if self.rb_multi.isChecked() else 2
        self.stack.setCurrentIndex(page_index)

    # -----------------------
    # Single click helpers
    # -----------------------
    def _update_mouse_label(self):
        x, y = pyautogui.position()
        self.mouse_pos_label.setText(f"Mouse: {x}, {y}")
        self.single_key_edit.setVisible(self.single_click_type.currentText() == 'key')

    def single_set_pos(self):
        QMessageBox.information(self, "Set Position", "Move mouse to desired position and press Enter.")
        captured = {'pos': None}
        def on_press(key):
            if key == Key.enter:
                captured['pos'] = pyautogui.position()
                return False
        with pynput_keyboard.Listener(on_press=on_press) as listener:
            listener.join()
        if captured['pos']:
            x, y = captured['pos']
            QMessageBox.information(self, "Captured", f"Captured: {x}, {y}")
            self._single_pos = (x, y)

    # -----------------------
    # Multi list management
    # -----------------------
    def _add_multi_item(self, action=None, *args, **kwargs):
        if action is None:
            x, y = pyautogui.position()
            action = ClickAction(x=x, y=y)
        widget = MultiItemWidget(action, remove_callback=self._remove_multi_item)
        self.multi_layout.insertWidget(self.multi_layout.count() - 1, widget)
        self.config.items.append(action)

    def _remove_multi_item(self, widget: MultiItemWidget):
        self.multi_layout.removeWidget(widget)
        widget.setParent(None)
        try:
            self.config.items.remove(widget.action)
        except ValueError:
            pass

    # -----------------------
    # Recording
    # -----------------------
    def _add_record_item_safe(self, text: str):
        QApplication.instance().postEvent(self, _FuncEvent(lambda: self.record_list.addItem(text)))

    def toggle_recording(self):
        if getattr(self, '_recording', False):
            self._recording = False
            self.record_btn.setText("Record")
        else:
            self._recorded_events = []
            self.record_list.clear()
            self._recording = True
            self.record_btn.setText("Stop Recording")
            threading.Thread(target=self._record_thread, daemon=True).start()

    def _record_thread(self):
        start_time = time.time()
        last_time = start_time

        def on_move(x, y):
            if not getattr(self, '_recording', False):
                return False
            nonlocal last_time
            now = time.time()
            self._recorded_events.append(('move', x, y, int((now-last_time)*1000)))
            last_time = now
            self._add_record_item_safe(f"Move to {x},{y}")

        def on_click(x, y, button, pressed):
            if not getattr(self, '_recording', False):
                return False
            nonlocal last_time
            now = time.time()
            self._recorded_events.append(('click', x, y, str(button.name), pressed, int((now-last_time)*1000)))
            last_time = now
            self._add_record_item_safe(f"{'Down' if pressed else 'Up'} {button.name} @ {x},{y}")

        def on_scroll(x, y, dx, dy):
            if not getattr(self, '_recording', False):
                return False
            nonlocal last_time
            now = time.time()
            self._recorded_events.append(('scroll', x, y, dx, dy, int((now-last_time)*1000)))
            last_time = now
            self._add_record_item_safe(f"Scroll {dx},{dy} @ {x},{y}")

        def on_key_press(key):
            if not getattr(self, '_recording', False):
                return False
            parsed_stop = self.config.stop_hotkey
            name = getattr(key, 'char', None) or f'<{getattr(key, "name", str(key))}>'
            if key == Key.enter or str(key).lower().find(parsed_stop.strip('<>')) != -1:
                self._recording = False
                QApplication.instance().postEvent(self, _FuncEvent(lambda: self.record_btn.setText("Record")))
                return False
            nonlocal last_time
            now = time.time()
            self._recorded_events.append(('key', name, int((now-last_time)*1000)))
            last_time = now
            self._add_record_item_safe(f"Key {name}")

        with pynput_mouse.Listener(on_move=on_move, on_click=on_click, on_scroll=on_scroll) as mlistener, \
             pynput_keyboard.Listener(on_press=on_key_press) as klistener:
            while getattr(self, '_recording', False):
                time.sleep(0.05)
        self._add_record_item_safe("Recording finished.")

    # -----------------------
    # Execution engine
    # -----------------------
    def start_execution(self):
        self.stop_event.clear()
        mode = self.stack.currentIndex()
        if mode == 0:
            # single
            pos = getattr(self, '_single_pos', None)
            if not pos:
                QMessageBox.warning(self, "No position", "No position set for single click. Use 'Set Position'.")
                return
            typ = self.single_click_type.currentText()
            delay = self.single_delay.value() / 1000.0
            keych = self.single_key_edit.text().strip() or None
            def run_single():
                while not self.stop_event.is_set():
                    x, y = pos
                    if typ == 'key':
                        if keych:
                            pyautogui.press(keych)
                    else:
                        pyautogui.click(x, y, button=typ)
                    # wait
                    time.sleep(delay)
            self.exec_thread = threading.Thread(target=run_single, daemon=True)
            self.exec_thread.start()
        elif mode == 1:
            # multi
            # build actions from widgets in layout
            items = []
            for i in range(self.multi_layout.count()-1):
                widget = self.multi_layout.itemAt(i).widget()
                if isinstance(widget, MultiItemWidget):
                    act = widget.action
                    # normalize delay to seconds
                    d = act.delay_ms / 1000.0 if act.unit == 'ms' else act.delay_ms
                    items.append((act, d))
            if not items:
                QMessageBox.warning(self, "No actions", "Add at least one action.")
                return

            def run_multi():
                idx = 0
                while not self.stop_event.is_set():
                    act, delay_s = items[idx]
                    # perform action
                    if act.action_type == 'key':
                        if act.key_char:
                            pyautogui.press(act.key_char)
                    else:
                        pyautogui.click(act.x, act.y, button=act.action_type)
                    # wait for delay
                    # allow early exit
                    for _ in range(int(delay_s * 100)):
                        if self.stop_event.is_set():
                            break
                        time.sleep(0.01)
                    idx = (idx + 1) % len(items)
            self.exec_thread = threading.Thread(target=run_multi, daemon=True)
            self.exec_thread.start()
        else:
            # record replay
            evs = getattr(self, '_recorded_events', None)
            if not evs:
                QMessageBox.warning(self, "No recording", "No recorded events to play.")
                return


            def run_recorded():
                while not self.stop_event.is_set():
                    for ev in evs:
                        if self.stop_event.is_set():
                            break
                        dt = ev[-1] / 1000.0  # last item is the delay in ms


                        if ev[0] == 'move':
                            _, x, y, _ = ev
                            time.sleep(dt)
                            pyautogui.moveTo(x, y)
                        elif ev[0] == 'click':
                            _, x, y, btn, pressed, _ = ev
                            time.sleep(dt)
                            if pressed:
                                pyautogui.mouseDown(x, y, button=btn)
                            else:
                                pyautogui.mouseUp(x, y, button=btn)
                        elif ev[0] == 'scroll':
                            _, x, y, dx, dy, _ = ev
                            time.sleep(dt)
                            pyautogui.scroll(dy)
                        elif ev[0] == 'key':
                            _, name, _ = ev
                            time.sleep(dt)
                            if name.startswith('<') and name.endswith('>'):
                                kn = name.strip('<>')
                                try:
                                    pyautogui.press(kn)
                                except Exception:
                                    pass
                            else:
                                pyautogui.press(name)

            self.exec_thread = threading.Thread(target=run_recorded, daemon=True)
            self.exec_thread.start()

    def stop_execution(self):
        self.stop_event.set()
        if self.exec_thread and self.exec_thread.is_alive():
            self.exec_thread.join(timeout=0.5)
        self.exec_thread = None

    # -----------------------
    # Settings and hotkeys
    # -----------------------
    def open_settings(self):
        dlg = SettingsDialog(self, config=self.config)
        if dlg.exec():
            # re-install hotkeys
            self._install_hotkeys()
            QMessageBox.information(self, "Saved", f"Hotkeys set: Start {self.config.start_hotkey}, Stop {self.config.stop_hotkey}")

    def _install_hotkeys(self):
        # stop previous listener
        if self.hotkey_listener:
            try:
                self.hotkey_listener.stop()
            except Exception:
                pass
            self.hotkey_listener = None

        # Map to actions using pynput's GlobalHotKeys
        start = self.config.start_hotkey or '<f9>'
        stop = self.config.stop_hotkey or '<f10>'

        # helper to convert '<f9>' -> '<f9>' or 'a' -> 'a' for pynput
        def conv(keystr):
            s = keystr.strip()
            if s.startswith('<') and s.endswith('>'):
                name = s.strip('<>')
                return f'<{name}>'
            return s

        mapping = {}
        mapping[conv(start)] = self._hotkey_start_pressed
        mapping[conv(stop)] = self._hotkey_stop_pressed

        # build hotkey dict in format required by GlobalHotKeys: {"<f9>": callback, "a": callback}
        try:
            self.hotkey_listener = pynput_keyboard.GlobalHotKeys(mapping)
            self.hotkey_listener.start()
        except Exception as e:
            print("Failed to start hotkey listener:", e)

    def _hotkey_start_pressed(self):
        # This runs in listener thread
        # start execution in main thread
        QApplication.instance().postEvent(self, _FuncEvent(self.start_execution))

    def _hotkey_stop_pressed(self):
        QApplication.instance().postEvent(self, _FuncEvent(self.stop_execution))

    # -----------------------
    # Save / Load
    # -----------------------
    def save_config_dialog(self):
        fname, _ = QFileDialog.getSaveFileName(self, "Save config", str(Path.home()), "JSON Files (*.json)")
        if not fname:
            return
        self.save_config(fname)

    def save_config(self, fname: str):
        # assemble config
        # update items from multi widget list
        items = []
        for i in range(self.multi_layout.count()-1):
            widget = self.multi_layout.itemAt(i).widget()
            if isinstance(widget, MultiItemWidget):
                items.append(asdict(widget.action))
        self.config.items = [ClickAction(**it) for it in items]
        self.config.last_saved = fname
        data = {
            'start_hotkey': self.config.start_hotkey,
            'stop_hotkey': self.config.stop_hotkey,
            'items': [asdict(i) for i in self.config.items],
        }
        with open(fname, 'w') as f:
            json.dump(data, f, indent=2)
        QMessageBox.information(self, "Saved", f"Saved to {fname}")

    def load_config_dialog(self):
        fname, _ = QFileDialog.getOpenFileName(self, "Load config", str(Path.home()), "JSON Files (*.json)")

        if not fname:
            return
        self.load_config(fname)

    def load_config(self, fname: str):
        with open(fname, 'r') as f:
            data = json.load(f)
        self.config.start_hotkey = data.get('start_hotkey', self.config.start_hotkey)
        self.config.stop_hotkey = data.get('stop_hotkey', self.config.stop_hotkey)
        self.config.items = [ClickAction(**it) for it in data.get('items', [])]
        # repopulate UI multi list
        # clear existing
        for i in reversed(range(self.multi_layout.count()-1)):
            widget = self.multi_layout.itemAt(i).widget()
            if widget:
                self.multi_layout.removeWidget(widget)
                widget.setParent(None)
        for it in self.config.items:
            self._add_multi_item(it)
        self._install_hotkeys()
        QMessageBox.information(self, "Loaded", f"Loaded {fname}")

    # -----------------------
    # Qt event handling for hotkey callbacks
    # -----------------------
    def customEvent(self, event):
        if isinstance(event, _FuncEvent):
            event.callback()

# ---------------------------
# Custom event to safely call functions on main thread
# ---------------------------
from PySide6.QtCore import QEvent
class _FuncEvent(QEvent):
    def __init__(self, callback):
        super().__init__(QEvent.User)
        self.callback = callback

# ---------------------------
# Entry point
# ---------------------------
def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()