"""Cross-platform exclusive process lock for browser operations."""
import os
import threading
from pathlib import Path
from contextlib import contextmanager

_held=threading.local()

@contextmanager
def worker_lock(root):
    p=Path(root)/'locks'/'worker.lock'; p.parent.mkdir(parents=True,exist_ok=True)
    key=str(p.resolve());held=getattr(_held,'paths',set())
    if key in held:
        yield
        return
    f=open(p,'a+b')
    try:
        if os.name=='nt':
            import msvcrt
            f.seek(0); f.write(b'0'); f.flush(); f.seek(0)
            msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        held.add(key);_held.paths=held
        try:yield
        finally:held.remove(key)
    finally:
        f.close()
