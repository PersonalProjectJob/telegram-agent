"""Read-only foreground inspection for the separate subscription worker.

Uses the canonical process mutex, existing lock file, OS flock, priority recheck
and cancellation primitives. Only marker garbage collection differs: this worker
can traverse /run/agentfleet and write ollama.lock, but cannot delete PID markers.
"""
from contextlib import contextmanager
import os
from pathlib import Path

from . import model_lock as shared

MAX_DIRECTORY_ENTRIES = 4096


def live_foreground_readonly(directory, *, stop=None):
    """Dead/invalid markers are ignored and retained; live PID wins priority."""
    live = False
    with os.scandir(directory) as entries:
        for count, marker in enumerate(entries, 1):
            if stop is not None and stop.is_set(): raise shared.LockStopped()
            if count > MAX_DIRECTORY_ENTRIES:
                raise OSError('Model lock directory inspection bound exceeded')
            if not marker.name.startswith(shared.FOREGROUND_PREFIX): continue
            suffix = marker.name[len(shared.FOREGROUND_PREFIX):]
            try:
                pid_text, random = suffix.split('.', 1)
                pid = int(pid_text)
                if random and 0 < pid < 2**31 and shared.pid_alive(pid): live = True
            except ValueError:
                continue
    return live


class SubscriptionModelLock(shared.ModelLock):
    @contextmanager
    def _enter(self, *, background=False, stop=None):
        if not background:
            raise ValueError('Subscription worker lock is background only')
        directory = Path(self.directory or os.environ.get(shared.LOCK_DIR_ENV, shared.DEFAULT_LOCK_DIR))
        fd = None
        local = locked = False
        try:
            while True:
                if stop is not None and stop.is_set(): raise shared.LockStopped()
                if not live_foreground_readonly(directory, stop=stop) and shared._PROCESS_LOCK.acquire(blocking=False):
                    local = True
                    fd = shared._open_lock(directory)
                    locked = shared._lock(fd, blocking=False)
                    if stop is not None and stop.is_set(): raise shared.LockStopped()
                    # Match canonical foreground priority after OS acquisition.
                    if locked and not live_foreground_readonly(directory, stop=stop): break
                    if locked:
                        shared._unlock(fd)
                        locked = False
                    os.close(fd)
                    fd = None
                    shared._PROCESS_LOCK.release()
                    local = False
                shared._wait(stop, self.poll_seconds)
            yield
        finally:
            try:
                if locked: shared._unlock(fd)
            finally:
                try:
                    if fd is not None: os.close(fd)
                finally:
                    if local: shared._PROCESS_LOCK.release()


SUBSCRIPTION_MODEL_LOCK = SubscriptionModelLock()
