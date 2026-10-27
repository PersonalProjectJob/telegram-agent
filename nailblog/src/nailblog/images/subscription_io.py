"""Descriptor-relative queue I/O. POSIX worker never re-resolves a job path."""
import json
import os
from pathlib import Path
import stat
import uuid

from . import ImageError


def valid_stat(info, *, directory=False, owner=None, limit=2*1024*1024, native=False):
    if (not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)) or
            (not directory and (info.st_nlink != 1 or info.st_size > limit)) or
            (os.name == 'posix' and (info.st_mode & (0o022 if native else 0o007) or
                                     (owner is not None and info.st_uid != owner)))):
        raise ImageError('cli_permissions_or_bounds')


class Directory:
    def __init__(self, path, *, owner=None, native=False, parent=None):
        self.path = Path(path) if parent is None else parent.path / path
        self.fd = None
        if parent is not None and (Path(path).name != path or path in ('.', '..')):
            raise ImageError('cli_path_forbidden')
        if not self.path.is_absolute() or '..' in self.path.parts:
            raise ImageError('cli_path_forbidden')
        if os.name != 'posix':
            if any(p.is_symlink() for p in (self.path, *self.path.parents)):
                raise ImageError('cli_path_forbidden')
            valid_stat(self.path.stat(), directory=True, owner=owner, native=native)
            return
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            if parent is not None:
                self.fd = os.open(path, flags, dir_fd=parent.fd)
            else:
                self.fd = os.open(self.path.anchor, flags)
                for part in self.path.parts[1:]:
                    next_fd = os.open(part, flags, dir_fd=self.fd)
                    os.close(self.fd)
                    self.fd = next_fd
            valid_stat(os.fstat(self.fd), directory=True, owner=owner, native=native)
        except OSError:
            self.close()
            raise ImageError('cli_path_forbidden') from None
        except BaseException:
            self.close()
            raise

    def __enter__(self): return self
    def __exit__(self, *_): self.close()
    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def _name(self, name):
        if not isinstance(name, str) or Path(name).name != name or name in ('.', '..'):
            raise ImageError('cli_path_forbidden')
        return name if self.fd is not None else self.path / name

    def exists(self, name):
        try:
            os.stat(self._name(name), dir_fd=self.fd, follow_symlinks=False)
            return True
        except FileNotFoundError: return False

    def names(self): return os.listdir(self.fd if self.fd is not None else self.path)

    def mkdir(self, name, mode=0o2770):
        os.mkdir(self._name(name), mode, dir_fd=self.fd)
        with Directory(name, parent=self) as directory:
            if directory.fd is not None: os.fchmod(directory.fd, mode)
            else: directory.path.chmod(mode)

    def read(self, name, *, owner=None, limit=2*1024*1024, native=False, info=False):
        try:
            fd = os.open(self._name(name), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0), dir_fd=self.fd)
        except OSError:
            raise ImageError('cli_path_forbidden') from None
        with os.fdopen(fd, 'rb') as stream:
            metadata = os.fstat(stream.fileno())
            valid_stat(metadata, owner=owner, limit=limit, native=native)
            data = stream.read(limit+1)
            if len(data) > limit: raise ImageError('cli_permissions_or_bounds')
        return (data, metadata) if info else data

    def atomic(self, name, value, *, raw=False):
        destination = self._name(name)
        data = value if raw else json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
        temporary = self._name('.write-' + uuid.uuid4().hex)
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o640, dir_fd=self.fd)
            with os.fdopen(fd, 'wb') as stream:
                # Producer keeps UMask0077. Queue protocol still requires the
                # narrow shared group to read published request/receipt files.
                if os.name == 'posix': os.fchmod(stream.fileno(), 0o640)
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, destination, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            if self.fd is not None: os.fsync(self.fd)
        finally:
            try: os.unlink(temporary, dir_fd=self.fd)
            except FileNotFoundError: pass
