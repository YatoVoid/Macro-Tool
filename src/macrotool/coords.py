"""Converting between Qt's logical coordinates and the backend's.

Qt places windows in logical (device independent) pixels. On X11 and Windows
the input backend works in physical pixels, so on a 150% screen logical
(100, 100) on the primary screen is physical (150, 150). Screen origins are
the same in both spaces. On macOS and Wayland the backend uses logical units
already.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRect
from PySide6.QtGui import QGuiApplication, QScreen


def _screens() -> list[QScreen]:
    return QGuiApplication.screens()


def _scale(screen: QScreen, native: bool) -> float:
    return screen.devicePixelRatio() if native else 1.0


def screen_at(p: QPointF) -> QScreen:
    screen = QGuiApplication.screenAt(p.toPoint())
    return screen or QGuiApplication.primaryScreen()


def to_native(p: QPointF, native: bool) -> tuple[int, int]:
    screen = screen_at(p)
    tl = screen.geometry().topLeft()
    s = _scale(screen, native)
    return round(tl.x() + (p.x() - tl.x()) * s), round(tl.y() + (p.y() - tl.y()) * s)


def native_rect(screen: QScreen, native: bool) -> QRect:
    g = screen.geometry()
    s = _scale(screen, native)
    return QRect(g.x(), g.y(), round(g.width() * s), round(g.height() * s))


def to_logical(x: int, y: int, native: bool) -> QPointF:
    for screen in _screens():
        r = native_rect(screen, native)
        if r.contains(QPoint(x, y)):
            tl = screen.geometry().topLeft()
            s = _scale(screen, native)
            return QPointF(tl.x() + (x - tl.x()) / s, tl.y() + (y - tl.y()) / s)
    return QPointF(x, y)


def desktop_bounds() -> QRect:
    """Bounding box of all screens in logical pixels."""
    rect = QRect()
    for screen in _screens():
        rect = rect.united(screen.geometry())
    return rect


def on_any_screen(x: int, y: int, native: bool) -> bool:
    return any(native_rect(s, native).contains(QPoint(x, y)) for s in _screens())
