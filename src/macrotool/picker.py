"""Full-screen point picker with a magnifier.

Modes:
  single  click (or Enter) picks one point
  multi   each click adds a point; Enter or right-click finishes
  edit    existing points can be dragged; Enter saves

The overlay shows a frozen screenshot when one can be taken, so the
magnifier shows real pixels. On Wayland desktops that allow no screenshot it
falls back to a tinted see-through overlay.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

from PySide6.QtCore import (QEasingCurve, QEventLoop, QObject, QPointF, QRect, QRectF, Qt,
                            QTimer, QVariantAnimation, Signal)
from PySide6.QtGui import (QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QImage,
                           QKeyEvent, QPainter, QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import QApplication, QWidget

from . import coords
from .backends import session_type

ACCENT = QColor("#ff5a36")
INK = QColor(18, 20, 26)
PAPER = QColor(250, 250, 247)

HINTS = {
    "single": "Click to pick  ·  Arrow keys nudge 1 px (Shift: 10)  ·  Enter picks  ·  Esc cancels",
    "multi": "Click to add points  ·  Backspace removes the last  ·  Enter or right-click finishes  ·  Esc cancels",
    "edit": "Drag a point to move it  ·  Arrow keys nudge the selected point  ·  Enter saves  ·  Esc cancels",
}


def capture_screens() -> dict[str, QPixmap]:
    """Screenshot of each screen, keyed by screen name. Empty if not possible."""
    screens = QGuiApplication.screens()
    shots: dict[str, QPixmap] = {}
    if sys.platform == "darwin" and not _mac_can_capture():
        return shots
    if session_type() != "wayland":
        for s in screens:
            pm = s.grabWindow(0)
            if not pm.isNull() and not _blank(pm):
                shots[s.name()] = pm
        return shots
    tool = None
    if shutil.which("spectacle") and "KDE" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper():
        tool = lambda path: ["spectacle", "-b", "-n", "-f", "-o", path]  # noqa: E731
    elif shutil.which("grim"):
        tool = lambda path: ["grim", path]  # noqa: E731
    if tool is None:
        return shots
    fd, path = tempfile.mkstemp(prefix="macrotool-shot-", suffix=".png")
    os.close(fd)
    try:
        subprocess.run(tool(path), capture_output=True, timeout=8)
        img = QImage(path)
    except (OSError, subprocess.SubprocessError):
        img = QImage()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    if img.isNull():
        return shots
    bounds = coords.desktop_bounds()
    sx = img.width() / max(1, bounds.width())
    sy = img.height() / max(1, bounds.height())
    for s in screens:
        g = s.geometry()
        crop = QRect(round((g.x() - bounds.x()) * sx), round((g.y() - bounds.y()) * sy),
                     round(g.width() * sx), round(g.height() * sy))
        pm = QPixmap.fromImage(img.copy(crop))
        pm.setDevicePixelRatio(sx)
        shots[s.name()] = pm
    return shots


def _mac_can_capture() -> bool:
    """Without Screen Recording permission macOS hands back only the
    wallpaper, which would make the magnifier lie."""
    try:
        import Quartz
        return bool(Quartz.CGPreflightScreenCaptureAccess())
    except Exception:
        return False


def _blank(pm: QPixmap) -> bool:
    img = pm.toImage().scaled(16, 16)
    first = img.pixel(0, 0)
    return all(img.pixel(x, y) == first for x in range(16) for y in range(16))


class Picker(QObject):
    """Shows the overlay on every screen. Emits finished(points) with native
    coordinates, or finished(None) when cancelled."""

    finished = Signal(object)

    def __init__(self, mode: str = "single", points: list[tuple[int, int]] | None = None,
                 native_pixels: bool = True, labels: list[str] | None = None,
                 screenshots: dict | None = None, parent=None):
        super().__init__(parent)
        assert mode in HINTS
        self.mode = mode
        self.native = native_pixels
        self.points: list[QPointF] = [coords.to_logical(x, y, native_pixels) for x, y in (points or [])]
        self.labels = labels
        self.cursor = QCursor.pos().toPointF()
        self.nudge = QPointF(0, 0)
        self.selected = len(self.points) - 1 if mode == "edit" and self.points else -1
        self.dragging = -1
        self.hover = -1
        self.zoom = 10
        self.ripple: tuple[QPointF, float] | None = None
        self.shots = screenshots if screenshots is not None else {}
        self.windows: list[Overlay] = []
        self._done = False
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(260)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_ripple)

    # ----- lifecycle
    def show(self):
        for screen in QGuiApplication.screens():
            w = Overlay(self, screen, self.shots.get(screen.name()))
            self.windows.append(w)
        for w in self.windows:
            w.present()
        target = self._window_at(self.cursor) or self.windows[0]
        target.activateWindow()
        target.setFocus()

    def _finish(self, result):
        if self._done:
            return
        self._done = True
        for w in self.windows:
            w.hide()
            w.deleteLater()
        self.windows = []
        self.finished.emit(result)

    def cancel(self):
        self._finish(None)

    def accept(self):
        if self.mode == "single":
            self._finish([coords.to_native(self.point, self.native)])
        else:
            self._finish([coords.to_native(p, self.native) for p in self.points])

    # ----- state
    @property
    def point(self) -> QPointF:
        return self.cursor + self.nudge

    def _window_at(self, p: QPointF):
        for w in self.windows:
            if QRectF(w.screen_geo).contains(p):
                return w
        return None

    def refresh(self):
        for w in self.windows:
            w.update()

    def _on_ripple(self, v):
        if self.ripple is not None:
            self.ripple = (self.ripple[0], float(v))
            if v >= 1.0:
                self.ripple = None
        self.refresh()

    def _start_ripple(self, p: QPointF):
        self.ripple = (QPointF(p), 0.0)
        self._anim.stop()
        self._anim.start()

    def hit(self, p: QPointF) -> int:
        best, best_d = -1, 14.0 ** 2
        for i, q in enumerate(self.points):
            d = (q.x() - p.x()) ** 2 + (q.y() - p.y()) ** 2
            if d <= best_d:
                best, best_d = i, d
        return best

    def moved(self, p: QPointF):
        self.cursor = p
        self.nudge = QPointF(0, 0)
        if self.dragging >= 0:
            self.points[self.dragging] = QPointF(p)
        elif self.mode == "edit":
            self.hover = self.hit(p)
        self.refresh()

    def pressed(self, p: QPointF, button):
        self.cursor = p
        self.nudge = QPointF(0, 0)
        if button == Qt.RightButton:
            if self.mode == "single":
                self.cancel()
            else:
                self.accept()
            return
        if button != Qt.LeftButton:
            return
        if self.mode == "single":
            self._start_ripple(p)
            QTimer.singleShot(140, self.accept)
        elif self.mode == "multi":
            self.points.append(QPointF(p))
            self._start_ripple(p)
        else:
            i = self.hit(p)
            if i >= 0:
                self.dragging = i
                self.selected = i
                self.points[i] = QPointF(p)
        self.refresh()

    def released(self, p: QPointF, button):
        if button == Qt.LeftButton and self.dragging >= 0:
            self._start_ripple(self.points[self.dragging])
            self.dragging = -1
            self.refresh()

    def wheel(self, delta: int):
        self.zoom = max(4, min(24, self.zoom + (2 if delta > 0 else -2)))
        self.refresh()

    def key(self, ev: QKeyEvent):
        k = ev.key()
        step = 10 if ev.modifiers() & Qt.ShiftModifier else 1
        arrows = {Qt.Key_Left: (-1, 0), Qt.Key_Right: (1, 0), Qt.Key_Up: (0, -1), Qt.Key_Down: (0, 1)}
        if k in arrows:
            dx, dy = arrows[k]
            win = self._window_at(self.point) or self.windows[0]
            unit = 1.0 / (win.screen().devicePixelRatio() if self.native else 1.0)
            delta = QPointF(dx * step * unit, dy * step * unit)
            if self.mode == "edit" and 0 <= self.selected < len(self.points):
                self.points[self.selected] += delta
            else:
                self.nudge += delta
            self.refresh()
        elif k in (Qt.Key_Return, Qt.Key_Enter):
            self.accept()
        elif k == Qt.Key_Space and self.mode == "multi":
            self.points.append(QPointF(self.point))
            self._start_ripple(self.point)
            self.refresh()
        elif k in (Qt.Key_Backspace, Qt.Key_Delete) and self.mode == "multi" and self.points:
            self.points.pop()
            self.refresh()
        elif k == Qt.Key_Tab and self.mode == "edit" and self.points:
            self.selected = (self.selected + 1) % len(self.points)
            self.refresh()
        elif k == Qt.Key_Escape:
            self.cancel()


class Overlay(QWidget):
    def __init__(self, picker: Picker, screen, shot: QPixmap | None):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.picker = picker
        self.shot = shot
        self.screen_geo = screen.geometry()
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setAttribute(Qt.WA_TranslucentBackground, shot is None)
        self.setMouseTracking(True)
        self.setCursor(Qt.BlankCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setWindowTitle("Pick a point")
        self._screen = screen
        self.setScreen(screen)
        self.setGeometry(self.screen_geo)
        self._shot_img = shot.toImage() if shot is not None else None
        self._font = QFont(self.font())
        self._font.setPointSizeF(max(9.0, self._font.pointSizeF()))
        self._mono = QFont("monospace")
        self._mono.setStyleHint(QFont.Monospace)
        self._mono.setPointSizeF(self._font.pointSizeF())

    def present(self):
        if sys.platform == "darwin":
            # full screen on macOS means a new Space with a slide animation
            self.setGeometry(self.screen_geo)
            self.show()
        else:
            self.showFullScreen()
        self.raise_()

    def screen(self):
        return self._screen

    # ----- events
    def _global(self, ev) -> QPointF:
        return ev.position() + QPointF(self.screen_geo.topLeft())

    def mouseMoveEvent(self, ev):
        self.picker.moved(self._global(ev))

    def mousePressEvent(self, ev):
        self.picker.pressed(self._global(ev), ev.button())

    def mouseReleaseEvent(self, ev):
        self.picker.released(self._global(ev), ev.button())

    def wheelEvent(self, ev):
        self.picker.wheel(ev.angleDelta().y())

    def keyPressEvent(self, ev):
        self.picker.key(ev)

    def enterEvent(self, ev):
        self.activateWindow()
        self.setFocus()

    def closeEvent(self, ev):
        if not self.picker._done:
            self.picker.cancel()
        super().closeEvent(ev)

    # ----- painting
    def paintEvent(self, ev):
        pk = self.picker
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        origin = QPointF(self.screen_geo.topLeft())
        if self.shot is not None:
            p.drawPixmap(0, 0, self.shot)
            p.fillRect(self.rect(), QColor(10, 12, 18, 46))
        else:
            p.fillRect(self.rect(), QColor(10, 12, 18, 70))

        self._draw_path(p, origin)
        here = QRectF(self.screen_geo).contains(pk.point)
        if here and pk.dragging < 0:
            self._draw_crosshair(p, pk.point - origin)
        if pk.ripple is not None:
            c, t = pk.ripple
            if QRectF(self.screen_geo).adjusted(-40, -40, 40, 40).contains(c):
                col = QColor(ACCENT)
                col.setAlphaF(1.0 - t)
                p.setPen(QPen(col, 2.5))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(c - origin, 6 + 22 * t, 6 + 22 * t)
        if here:
            self._draw_loupe(p, pk.point - origin)
        self._draw_hint(p)
        p.end()

    def _draw_path(self, p: QPainter, origin: QPointF):
        pk = self.picker
        pts = [q - origin for q in pk.points]
        if len(pts) > 1:
            pen = QPen(QColor(255, 255, 255, 200), 1.5, Qt.DashLine)
            pen.setDashPattern([4, 4])
            p.setPen(pen)
            path = QPainterPath(pts[0])
            for q in pts[1:]:
                path.lineTo(q)
            p.drawPath(path)
        p.setFont(self._font)
        fm = QFontMetrics(self._font)
        for i, q in enumerate(pts):
            active = i in (pk.selected, pk.hover, pk.dragging)
            r = 11 if active else 9
            p.setPen(QPen(PAPER, 2))
            p.setBrush(ACCENT if active or pk.mode != "edit" else QColor(ACCENT.red(), ACCENT.green(), ACCENT.blue(), 200))
            p.drawEllipse(q, r, r)
            p.setPen(PAPER)
            label = pk.labels[i] if pk.labels and i < len(pk.labels) else str(i + 1)
            w = fm.horizontalAdvance(label)
            if w <= 2 * r - 4:
                p.drawText(QRectF(q.x() - r, q.y() - r, 2 * r, 2 * r), Qt.AlignCenter, label)
            else:
                p.drawText(QRectF(q.x() + r + 3, q.y() - r, w + 4, 2 * r), Qt.AlignVCenter, label)

    def _draw_crosshair(self, p: QPainter, c: QPointF):
        gap = 7
        w, h = self.width(), self.height()
        segs = [(QPointF(0, c.y()), QPointF(c.x() - gap, c.y())),
                (QPointF(c.x() + gap, c.y()), QPointF(w, c.y())),
                (QPointF(c.x(), 0), QPointF(c.x(), c.y() - gap)),
                (QPointF(c.x(), c.y() + gap), QPointF(c.x(), h))]
        p.setRenderHint(QPainter.Antialiasing, False)
        for color, width in ((QColor(0, 0, 0, 120), 3), (QColor(255, 255, 255, 230), 1)):
            p.setPen(QPen(color, width))
            for a, b in segs:
                p.drawLine(a, b)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(QPen(ACCENT, 1.6))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(c, 3.5, 3.5)

    def _sample(self, c: QPointF):
        """Native-pixel region around c from the screenshot, and the colour under c."""
        img = self._shot_img
        if img is None:
            return None, None
        dpr = self.shot.devicePixelRatio()
        n = 15
        cx, cy = int(c.x() * dpr), int(c.y() * dpr)
        region = img.copy(cx - n // 2, cy - n // 2, n, n)
        color = QColor(img.pixel(cx, cy)) if 0 <= cx < img.width() and 0 <= cy < img.height() else None
        return region, color

    def _draw_loupe(self, p: QPainter, c: QPointF):
        pk = self.picker
        region, color = self._sample(c)
        native = coords.to_native(pk.point, pk.native)
        text = f"{native[0]}, {native[1]}"
        size = 15 * pk.zoom if region is not None else 0
        fm = QFontMetrics(self._mono)
        label_w = max(size, fm.horizontalAdvance(text) + (34 if color else 20))
        label_h = fm.height() + 12
        total_h = size + (8 if size else 0) + label_h
        box_w = max(size, label_w)
        x = c.x() + 26
        y = c.y() + 26
        if x + box_w > self.width() - 8:
            x = c.x() - 26 - box_w
        if y + total_h > self.height() - 8:
            y = c.y() - 26 - total_h
        if region is not None:
            rect = QRectF(x, y, size, size)
            clip = QPainterPath()
            clip.addRoundedRect(rect, 10, 10)
            p.save()
            p.setClipPath(clip)
            p.fillRect(rect, INK)
            p.setRenderHint(QPainter.SmoothPixmapTransform, False)
            p.drawImage(rect, region)
            grid = QColor(255, 255, 255, 28)
            p.setPen(QPen(grid, 1))
            for i in range(1, 15):
                p.drawLine(QPointF(x + i * pk.zoom, y), QPointF(x + i * pk.zoom, y + size))
                p.drawLine(QPointF(x, y + i * pk.zoom), QPointF(x + size, y + i * pk.zoom))
            mid = QRectF(x + 7 * pk.zoom, y + 7 * pk.zoom, pk.zoom, pk.zoom)
            p.setPen(QPen(QColor(0, 0, 0, 160), 3))
            p.setBrush(Qt.NoBrush)
            p.drawRect(mid)
            p.setPen(QPen(ACCENT, 1.5))
            p.drawRect(mid)
            p.restore()
            p.setPen(QPen(QColor(255, 255, 255, 220), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect, 10, 10)
            y += size + 8
        label = QRectF(x, y, box_w, label_h)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(INK.red(), INK.green(), INK.blue(), 235))
        p.drawRoundedRect(label, 7, 7)
        tx = x + 10
        if color is not None:
            sw = QRectF(tx, y + (label_h - 12) / 2, 12, 12)
            p.setBrush(color)
            p.setPen(QPen(QColor(255, 255, 255, 140), 1))
            p.drawRoundedRect(sw, 3, 3)
            tx += 20
        p.setFont(self._mono)
        p.setPen(PAPER)
        p.drawText(QRectF(tx, y, box_w - (tx - x) - 6, label_h), Qt.AlignVCenter | Qt.AlignLeft, text)

    def _draw_hint(self, p: QPainter):
        pk = self.picker
        primary = QGuiApplication.primaryScreen()
        if self._screen is not primary and len(pk.windows) > 1:
            return
        text = HINTS[pk.mode]
        if pk.mode == "multi" and pk.points:
            text = f"{len(pk.points)} point{'s' if len(pk.points) != 1 else ''}  ·  " + text
        p.setFont(self._font)
        fm = QFontMetrics(self._font)
        w = min(self.width() - 32, fm.horizontalAdvance(text) + 36)
        h = fm.height() + 18
        r = QRectF((self.width() - w) / 2, 22, w, h)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(INK.red(), INK.green(), INK.blue(), 225))
        p.drawRoundedRect(r, h / 2, h / 2)
        p.setPen(PAPER)
        p.drawText(r, Qt.AlignCenter, fm.elidedText(text, Qt.ElideRight, int(w - 24)))


def pick(mode="single", points=None, native_pixels=True, labels=None, hide: list[QWidget] | None = None):
    """Blocking helper: hide the given windows, run the picker, restore them.
    Returns a list of native points, or None if cancelled."""
    hidden = [w for w in (hide or []) if w.isVisible()]
    for w in hidden:
        w.hide()
    app = QApplication.instance()
    if hidden:
        # let the compositor finish hiding before the screenshot
        loop = QEventLoop()
        QTimer.singleShot(260, loop.quit)
        loop.exec()
    shots = capture_screens()
    picker = Picker(mode, points, native_pixels, labels, screenshots=shots)
    result = []
    loop = QEventLoop()

    def done(r):
        result.append(r)
        loop.quit()
    picker.finished.connect(done)
    picker.show()
    loop.exec()
    for w in hidden:
        w.show()
        w.raise_()
        w.activateWindow()
    app.processEvents()
    picker.deleteLater()
    return result[0] if result else None
