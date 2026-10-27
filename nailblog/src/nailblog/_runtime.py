"""Private nonblocking service/session singleton on POSIX and Windows."""
import errno
import importlib
import os
from pathlib import Path


class AlreadyRunning(RuntimeError):
    """Another process owns this session/service directory."""


class ProcessLock:
    def __init__(self, directory, *, lock_module=None):
        self.directory=Path(directory)
        self.module=lock_module
        self.stream=None

    def __enter__(self):
        if self.module is None:
            self.module = importlib.import_module('msvcrt') if os.name == 'nt' else importlib.import_module('fcntl')
        self.stream=(self.directory/'nailblog.lock').open('a+b')
        try:
            os.chmod(self.stream.name,0o600)
            if hasattr(self.module,'flock'):
                self.module.flock(self.stream.fileno(),self.module.LOCK_EX|self.module.LOCK_NB)
            else:
                if os.fstat(self.stream.fileno()).st_size==0:
                    self.stream.write(b'\0');self.stream.flush()
                self.stream.seek(0)
                self.module.locking(self.stream.fileno(),self.module.LK_NBLCK,1)
        except BaseException as error:
            self.stream.close();self.stream=None
            if isinstance(error,OSError) and error.errno in (errno.EACCES,errno.EAGAIN,errno.EDEADLK):
                raise AlreadyRunning() from None
            raise
        return self

    def __exit__(self,*args):
        if self.stream is not None:
            try:
                if hasattr(self.module,'flock'):
                    self.module.flock(self.stream.fileno(),self.module.LOCK_UN)
                else:
                    self.stream.seek(0)
                    self.module.locking(self.stream.fileno(),self.module.LK_UNLCK,1)
            finally:
                self.stream.close();self.stream=None
