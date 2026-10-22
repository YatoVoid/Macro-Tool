"""Desktop app entry point."""

from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

from PySide6.QtCore import QLockFile, QStandardPaths, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from . import __version__
from .backends import BackendError, create, session_type

ICON = Path(__file__).with_name("icon.png")
SOCKET = "macro-tool-" + (os.environ.get("USER") or os.environ.get("USERNAME") or "user")


def _notify_running(path: str | None) -> bool:
    sock = QLocalSocket()
    sock.connectToServer(SOCKET)
    if not sock.waitForConnected(500):
        return False
    sock.write(("open " + path if path else "show").encode())
    sock.waitForBytesWritten(500)
    sock.disconnectFromServer()
    return True


def setup_backend(app):
    pointer = None
    note = ""
    if session_type() == "wayland" and os.environ.get("MACRO_TOOL_BACKEND", "uinput") != "pynput":
        from .backends.wayland_pointer import detect
        pointer = detect()
        note = f"pointer from {pointer.name}" if pointer else "no pointer position on this desktop"
    backend = create(pointer_source=pointer)
    if backend.name == "uinput":
        from . import coords

        def sync():
            b = coords.desktop_bounds()
            backend.set_bounds(b.x(), b.y(), b.width(), b.height())
        sync()
        for s in app.screens():
            s.geometryChanged.connect(lambda *_: sync())
        app.screenAdded.connect(lambda s: (s.geometryChanged.connect(lambda *_: sync()), sync()))
        app.screenRemoved.connect(lambda *_: QTimer.singleShot(0, sync))
    return backend, note


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if "--version" in argv:
        print(f"Macro Tool {__version__}")
        return 0
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(argv)
    app.setApplicationName("Macro Tool")
    app.setOrganizationName("YatoVoid")
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("io.github.yatovoid.MacroTool")
    if ICON.exists():
        app.setWindowIcon(QIcon(str(ICON)))
    file_arg = next((a for a in argv[1:] if not a.startswith("-")), None)

    lock_dir = Path(QStandardPaths.writableLocation(QStandardPaths.AppConfigLocation))
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(lock_dir / "instance.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        if _notify_running(str(Path(file_arg).resolve()) if file_arg else None):
            return 0
        lock.removeStaleLockFile()
        if not lock.tryLock(100):
            QMessageBox.critical(None, "Macro Tool", "Macro Tool is already running.")
            return 1

    try:
        backend, note = setup_backend(app)
    except BackendError as e:
        QMessageBox.critical(None, "Macro Tool can't send input", str(e))
        return 1

    from .window import MainWindow
    win = MainWindow(backend, pointer_note=note)

    server = QLocalServer()
    QLocalServer.removeServer(SOCKET)
    server.listen(SOCKET)

    def on_connection():
        sock = server.nextPendingConnection()
        if sock is None:
            return

        def read():
            msg = bytes(sock.readAll()).decode(errors="replace")
            win.showNormal()
            win.raise_()
            win.activateWindow()
            if msg.startswith("open ") and win._confirm_discard():
                win.open_path(Path(msg[5:]))
        sock.readyRead.connect(read)
    server.newConnection.connect(on_connection)

    win.show()
    if file_arg:
        win.open_path(Path(file_arg))

    # Ctrl+C in a terminal should quit cleanly and release held input.
    signal.signal(signal.SIGINT, lambda *_: win.close())
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: win.close())
    tick = QTimer()
    tick.start(250)
    tick.timeout.connect(lambda: None)

    code = app.exec()
    server.close()
    backend.close()
    if getattr(backend, "pointer_source", None) is not None:
        backend.pointer_source.close()
    lock.unlock()
    return code


if __name__ == "__main__":
    sys.exit(main())
