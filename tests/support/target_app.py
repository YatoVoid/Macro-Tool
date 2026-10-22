"""A window that logs every click, scroll and key it receives as JSON lines.

Used by the X11 integration tests: Macro Tool plays input, this window
records what actually arrived.
"""
import json
import sys
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

BUTTONS = {Qt.LeftButton: "left", Qt.RightButton: "right", Qt.MiddleButton: "middle",
           Qt.BackButton: "back", Qt.ForwardButton: "forward"}


class Target(QWidget):
    def __init__(self, log_path, geometry):
        super().__init__(None, Qt.FramelessWindowHint)
        self.log = open(log_path, "a", buffering=1)
        self.setGeometry(*geometry)
        self.setMouseTracking(False)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setStyleSheet("background: #e9e6df")

    def write(self, **kw):
        kw["t"] = time.monotonic()
        self.log.write(json.dumps(kw) + "\n")

    def mousePressEvent(self, ev):
        g = ev.globalPosition()
        self.write(type="press", button=BUTTONS.get(ev.button(), "?"), x=round(g.x()), y=round(g.y()))

    def mouseReleaseEvent(self, ev):
        g = ev.globalPosition()
        self.write(type="release", button=BUTTONS.get(ev.button(), "?"), x=round(g.x()), y=round(g.y()))

    def mouseDoubleClickEvent(self, ev):
        self.mousePressEvent(ev)

    def wheelEvent(self, ev):
        self.write(type="wheel", dy=ev.angleDelta().y(), dx=ev.angleDelta().x())

    def keyPressEvent(self, ev):
        self.write(type="key", key=ev.key(), text=ev.text(), mods=int(ev.modifiers().value))


if __name__ == "__main__":
    app = QApplication(sys.argv[:1])
    geo = tuple(int(v) for v in sys.argv[2].split(",")) if len(sys.argv) > 2 else (0, 0, 1280, 800)
    w = Target(sys.argv[1], geo)
    w.show()
    w.activateWindow()
    w.setFocus()
    app.exec()
