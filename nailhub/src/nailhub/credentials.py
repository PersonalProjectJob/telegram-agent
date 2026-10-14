"""Read Owner-authorized local login material without following symlinks or logging values."""

import json
import os
import stat
from pathlib import Path

from .validation import NeedsHuman


def load_credentials(path):
    if not path:
        raise NeedsHuman('login_required: no credential file configured')
    target = Path(path)
    try:
        info = target.lstat()
        if not stat.S_ISREG(info.st_mode) or target.is_symlink():
            raise NeedsHuman('credential file must be regular and not a symlink')
        if os.name == 'posix' and (stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid()):
            raise NeedsHuman('credential file requires service ownership and mode 0600')
        fd = os.open(target, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'r', encoding='utf-8') as stream:
            actual = os.fstat(stream.fileno())
            if (actual.st_ino, actual.st_dev) != (info.st_ino, info.st_dev):
                raise NeedsHuman('credential file changed during open')
            if os.name == 'posix' and (stat.S_IMODE(actual.st_mode) != 0o600 or actual.st_uid != os.getuid()):
                raise NeedsHuman('credential file permissions changed during open')
            data = json.loads(stream.read(16385))
        if (not isinstance(data, dict) or set(data) != {'email', 'password'}
                or any(not isinstance(v, str) or not v or len(v) > 4096 for v in data.values())):
            raise NeedsHuman('invalid credential file schema')
        return data
    except NeedsHuman:
        raise
    except Exception:
        raise NeedsHuman('credential file could not be read safely') from None
