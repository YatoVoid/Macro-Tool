"""Command line: play or check a macro file without the window."""

from __future__ import annotations

import argparse
import signal
import sys
import threading

from . import __version__, keys, model
from .backends import BackendError, create, session_type
from .engine import Player

EPILOG = """\
examples:
  macro-tool-cli play farm.json
  macro-tool-cli play farm.json --repeat 0 --stop-after 3600
  macro-tool-cli check farm.json

Press the stop hotkey (F10 by default) or Ctrl+C to stop.
exit codes: 0 done or stopped, 1 playback error, 2 bad file or arguments,
3 input not available
"""


def build_parser():
    p = argparse.ArgumentParser(prog="macro-tool-cli", description="Play Macro Tool macros from a terminal.",
                                epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"Macro Tool {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    play = sub.add_parser("play", help="play a macro file")
    play.add_argument("file")
    play.add_argument("--repeat", type=int, help="times to play it; 0 repeats until stopped")
    play.add_argument("--stop-after", type=float, metavar="SECONDS", help="stop after this many seconds")
    play.add_argument("--speed", type=float, help="speed multiplier, e.g. 2 plays twice as fast")
    play.add_argument("--delay", type=float, default=0, metavar="SECONDS", help="wait before starting")
    play.add_argument("--stop-key", default="f10", help="global key that stops playback (default F10, 'none' to disable)")
    play.add_argument("--screen", metavar="WxH", help="Wayland without a desktop session: the screen size")
    play.add_argument("--quiet", "-q", action="store_true")
    check = sub.add_parser("check", help="check that a macro file is valid")
    check.add_argument("file")
    return p


def _bounds_for_uinput(args):
    if args.screen:
        w, _, h = args.screen.lower().partition("x")
        return 0, 0, int(w), int(h)
    if session_type() == "wayland":
        from PySide6.QtGui import QGuiApplication
        app = QGuiApplication.instance() or QGuiApplication([sys.argv[0]])
        rect = None
        for s in app.screens():
            rect = s.geometry() if rect is None else rect.united(s.geometry())
        if rect is not None:
            return rect.x(), rect.y(), rect.width(), rect.height()
    return None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        macro = model.load(args.file)
    except (OSError, model.MacroError, ValueError) as e:
        print(f"macro-tool-cli: {args.file}: {e}", file=sys.stderr)
        return 2
    if args.cmd == "check":
        print(f"{args.file}: {len(macro.steps)} steps, "
              f"{'repeats until stopped' if macro.repeat == 0 else f'plays {macro.repeat} time(s)'}")
        return 0

    if args.repeat is not None:
        macro.repeat = max(0, args.repeat)
    if args.stop_after is not None:
        macro.stop_after_s = max(0.0, args.stop_after)
    if args.speed is not None:
        if not 0.1 <= args.speed <= 100:
            print("macro-tool-cli: --speed must be between 0.1 and 100", file=sys.stderr)
            return 2
        macro.speed = args.speed
    try:
        stop_combo = None if args.stop_key.lower() == "none" else keys.parse_combo(args.stop_key)
        backend = create()
        if backend.name == "uinput":
            bounds = _bounds_for_uinput(args)
            if bounds:
                backend.set_bounds(*bounds)
    except (BackendError, keys.KeyNameError, ValueError) as e:
        print(f"macro-tool-cli: {e}", file=sys.stderr)
        return 3 if isinstance(e, BackendError) else 2

    stop = threading.Event()
    player = Player(backend, macro)
    listener = None
    if stop_combo:
        matcher = keys.ComboMatcher()
        matcher.set_combos({stop_combo: "stop"})

        def on_event(ev):
            if ev.kind == "key":
                if ev.down and matcher.press(ev.key) == "stop":
                    player.stop(wait_s=0)
                elif not ev.down:
                    matcher.release(ev.key)
        try:
            listener = backend.listener(on_event)
            listener.start()
        except BackendError as e:
            listener = None
            if not args.quiet:
                print(f"macro-tool-cli: stop key unavailable ({e.args[0].splitlines()[0]}); use Ctrl+C",
                      file=sys.stderr)

    signal.signal(signal.SIGINT, lambda *_: player.stop(wait_s=0))
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: player.stop(wait_s=0))
    if args.delay > 0 and stop.wait(args.delay):
        return 0
    if not args.quiet:
        hint = f" Press {keys.format_combo(stop_combo)} or Ctrl+C to stop." if stop_combo and listener else " Press Ctrl+C to stop."
        print(f"Playing {args.file}.{hint}", file=sys.stderr)
    player.start()
    while player.running:
        player._thread.join(0.2)
    result = player.result
    if listener is not None:
        listener.stop()
    backend.close()
    if result is None:
        return 1
    if not args.quiet:
        print(f"{result.reason}: {result.loops} loop(s), {result.actions} action(s) in {result.seconds:.1f} s",
              file=sys.stderr)
    if result.reason == "error":
        print(f"macro-tool-cli: {result.error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
