# Contributing

## Running it

```
python -m venv .venv
.venv/bin/pip install -e . pytest
.venv/bin/macro-tool
```

On Windows use `.venv\Scripts\` instead of `.venv/bin/`. On Wayland, do the Wayland setup from the README first.

## Layout

| Path | What's there |
|---|---|
| `src/macrotool/model.py` | Steps, macros and the JSON file format, including loading old configs |
| `src/macrotool/engine.py` | Plays a macro on a background thread |
| `src/macrotool/recorder.py` | Turns raw input events into tidy steps |
| `src/macrotool/backends/` | Sending and reading input: `pynput_backend.py` for X11, Windows and macOS, `uinput_backend.py` for Wayland |
| `src/macrotool/picker.py` | The full-screen point picker |
| `src/macrotool/window.py`, `widgets.py` | The main window and dialogs |
| `src/macrotool/cli.py` | `macro-tool-cli` |
| `packaging/` | Desktop file, udev rule, icons, installer and AUR files |

## Before opening a PR

- Run `python -m pytest`. Add a test for the change where one fits.
- If you touched input, try it for real. `tests/test_x11.py` does that on X11 under Xvfb (`MACRO_TOOL_X11=1 xvfb-run pytest tests/test_x11.py`). Say in the PR which systems you tried it on.
- Keep the macro file format backward compatible. If a change can't be, bump `VERSION` in `model.py` and say why in the PR.

## Reporting a bug

Use the bug report template. The most useful details are the system and desktop, how you installed it, the input method shown in the bottom right corner of the window, and the macro file if one is involved.
