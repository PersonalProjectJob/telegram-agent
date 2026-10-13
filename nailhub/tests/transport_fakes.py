"""Offline Telethon surfaces: events.NewMessage and errors.FloodWaitError.seconds."""
from types import SimpleNamespace


class FakeFloodWaitError(Exception):
    def __init__(self, request=None, capture=0):
        super().__init__('synthetic FloodWait')
        self.request = request
        self.seconds = int(capture)


def fake_telethon(builder):
    return SimpleNamespace(events=SimpleNamespace(NewMessage=builder),
                           errors=SimpleNamespace(FloodWaitError=FakeFloodWaitError))


class FakeClock:
    """Matches wait_for_stop(stop, seconds), advancing time without a real sleep."""
    def __init__(self, on_wait=None):
        self.now = 0
        self.delays = []
        self.on_wait = on_wait

    async def wait(self, stop, seconds):
        self.delays.append(seconds)
        if not stop.is_set():
            self.now += seconds
            if self.on_wait:
                self.on_wait(stop, seconds)
        return stop.is_set()
