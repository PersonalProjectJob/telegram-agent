"""Serialize CPU model calls; queued foreground triage takes priority."""

import threading
from contextlib import contextmanager


class ModelGate:
    def __init__(self):
        self._condition = threading.Condition()
        self._active = False
        self._foreground = 0

    @contextmanager
    def enter(self, *, background=False):
        with self._condition:
            if not background:
                self._foreground += 1
            try:
                while self._active or (background and self._foreground):
                    self._condition.wait()
                self._active = True
            finally:
                if not background:
                    self._foreground -= 1
        try:
            yield
        finally:
            with self._condition:
                self._active = False
                self._condition.notify_all()


MODEL_GATE = ModelGate()
