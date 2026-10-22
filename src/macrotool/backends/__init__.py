from __future__ import annotations

import os
import sys

from .base import Backend, BackendError, InputEvent, Listener


def session_type() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_SESSION_TYPE") == "wayland":
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "console"


def create(pointer_source=None, choice: str | None = None) -> Backend:
    """Pick the backend for this session.

    MACRO_TOOL_BACKEND=pynput|uinput overrides the choice.
    """
    choice = (choice or os.environ.get("MACRO_TOOL_BACKEND") or "").lower()
    if not choice:
        choice = "uinput" if session_type() in ("wayland", "console") else "pynput"
    if choice == "uinput":
        from .uinput_backend import UinputBackend
        return UinputBackend(pointer_source=pointer_source)
    if choice == "pynput":
        from .pynput_backend import PynputBackend
        return PynputBackend()
    raise BackendError(f"Unknown backend {choice!r}; use pynput or uinput")


__all__ = ["Backend", "BackendError", "InputEvent", "Listener", "create", "session_type"]
