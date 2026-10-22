import time

from macrotool.backends.base import Backend, Listener


class FakeBackend(Backend):
    name = "fake"
    description = "Test"

    def __init__(self, fail_on=None):
        super().__init__()
        self.log = []
        self.pos = (0, 0)
        self.fail_on = fail_on

    def _record(self, *entry):
        if self.fail_on and entry[0] == self.fail_on:
            raise RuntimeError(f"{entry[0]} failed")
        self.log.append((time.monotonic(),) + entry)

    def position(self):
        return self.pos

    def _move(self, x, y):
        self._record("move", x, y)
        self.pos = (x, y)

    def _button(self, button, down):
        self._record("button", button, down)

    def _scroll(self, dx, dy):
        self._record("scroll", dx, dy)

    def _key(self, key, down):
        self._record("key", key, down)

    def _type(self, text):
        self._record("type", text)

    def listener(self, handler):
        return FakeListener(handler)

    def entries(self, kind=None):
        return [e[1:] for e in self.log if kind is None or e[1] == kind]


class FakeListener(Listener):
    started = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False
