# Macro Tool

A powerful cross-platform **macro recording and automation tool** for Linux (X11).
Easily record mouse and keyboard actions, create multi-step macros, and replay them with configurable delays and hotkeys.

---
<img width="704" height="542" alt="image" src="https://github.com/user-attachments/assets/b522aca0-e55d-4a66-a336-06290cf18143" />
<img width="696" height="548" alt="image" src="https://github.com/user-attachments/assets/908042db-fb90-4664-8100-8a37adfdbd88" />
<img width="693" height="540" alt="image" src="https://github.com/user-attachments/assets/14271cab-82a9-4143-bbcf-cad263b5d07b" />


## Features

* **Single Click Mode**

  * Click a fixed screen position repeatedly.
  * Supports left, right, middle mouse buttons or keyboard keys.
  * Configurable delay (ms or s) between actions.

* **Multi Click Mode**

  * Add multiple mouse/keyboard actions to a list.
  * Each action has position, click type, delay, and optional key press.
  * Easily reorder, remove, or set positions interactively.

* **Recording Mode**

  * Record mouse movements, clicks, scrolls, and keyboard inputs globally.
  * Replay recorded events in order with configurable speed.
  * Stop recording via configurable hotkey or Enter.

* **Hotkeys**

  * Start and stop macros using configurable global hotkeys.
  * Works across the system (requires X11).

* **Configuration**

  * Save and load macros as JSON.
  * Persistent hotkeys and action lists.

* **User Interface**

  * Qt-based GUI with intuitive controls.
  * Mouse position display.
  * Action lists with editable parameters.

---

## Installation

Requires Python 3.13+ and X11 session. **Does not work on Wayland.**

```bash
git clone <repo_url>
cd AutoClicker
python3 run_macro.py
```

This will:

* Create a virtual environment (`venv`)
* Upgrade pip
* Install dependencies: `PySide6`, `pyautogui`, `pynput`

---

## Run

Activate the virtual environment and start the app:

```bash
source venv/bin/activate
python3 AutoClicker.py
```

Or directly with venv Python:

```bash
./venv/bin/python AutoClicker.py
```

---

## Notes

* Only works on **X11 sessions**; Wayland does not allow global mouse/keyboard capture.
* PyAutoGUI and pynput may require root access if capturing input globally on Linux.
* Mouse/keyboard fails to capture outside the app on Wayland due to OS restrictions.

---

## License

MIT License
