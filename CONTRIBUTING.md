# Contributing

## Running it

```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install PySide6 pyautogui pynput
python3 AutoClicker.py
```

Requires an X11 session, Wayland doesn't allow the global mouse/keyboard
capture this depends on. `pyautogui`/`pynput` may need root to capture input
globally on Linux.

## Before opening a PR

- Test on an actual X11 session, not just that it imports cleanly.
- If you're picking up one of the open ToDo items in the README (save/load
  for multiple actions, smoother recording), say so in the PR so two people
  don't build the same thing.
- Keep the config JSON format backward compatible, or say clearly in the PR
  that it isn't and why.

## Reporting a bug

What you were doing (single click, multi click, or recording mode), what
you expected, what happened, and whether you're on X11 or Wayland.
