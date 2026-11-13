"""Production state bootstrap. This does not authorize production publication."""
import json
import os
import secrets
import sqlite3
import stat
from contextlib import closing
from pathlib import Path

from .state import StateError

PRODUCTION_ROOT = Path('/var/lib/agentfleet/nailblog/prod')
DATABASE_NAME = 'nailblog.sqlite3'
MARKER_NAME = 'state-binding.json'


def check_production_runs(config, *, required=False):
    """Require a provisioned private run root before state or artifact mutation."""
    if (config.mode != 'live' or config.contract.environment != 'production' or
            (not required and not config.autoflow.enabled)):
        return
    root = PRODUCTION_ROOT
    runs = Path(config.autoflow.runs_dir)
    try:
        if os.name != 'posix' or runs != root / 'runs':
            raise StateError('production_runs_invalid')
        for directory in (root, runs):
            info = directory.lstat()
            if (not stat.S_ISDIR(info.st_mode) or directory.resolve() != directory or
                    info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700):
                raise StateError('production_runs_invalid')
    except OSError:
        raise StateError('production_runs_invalid') from None


def ensure_production_state(path, *, root=None):
    """Bind a new database inode before State opens it; refuse unbound or replaced files.

    The provisioned root must be private to the producer. A crash during bootstrap
    leaves an incomplete pair and fails closed; recovery requires an operator.
    An identity is also stored in SQLite to detect replacement of the contents in
    place. This assumes no untrusted actor can write as the producer or root.
    Production remains disabled pending independent verification.
    """
    root = Path(root) if root is not None else PRODUCTION_ROOT
    path = Path(path)
    if os.name != 'posix' or not root.is_absolute() or path != root / DATABASE_NAME:
        raise StateError('production_state_path_invalid')
    try:
        info = root.lstat()
        if (not stat.S_ISDIR(info.st_mode) or root.resolve() != root or
                info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700):
            raise StateError('production_state_root_invalid')
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        directory = os.open(root, flags)
    except OSError:
        raise StateError('production_state_root_invalid') from None
    try:
        try:
            db_info = os.stat(DATABASE_NAME, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            for name in (MARKER_NAME, DATABASE_NAME + '-wal', DATABASE_NAME + '-shm'):
                try:
                    os.stat(name, dir_fd=directory, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                raise StateError('production_state_incomplete')
            fd = os.open(DATABASE_NAME, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            try:
                db_info = os.fstat(fd)
                os.fsync(fd)
            finally:
                os.close(fd)
            nonce = secrets.token_hex(32)
            with closing(sqlite3.connect(path)) as db:
                with db:
                    db.execute('CREATE TABLE production_state_binding (nonce TEXT NOT NULL)')
                    db.execute('INSERT INTO production_state_binding VALUES (?)', (nonce,))
            payload = json.dumps({'version': 1, 'environment': 'production', 'device': db_info.st_dev,
                                  'inode': db_info.st_ino, 'nonce': nonce}, sort_keys=True).encode('ascii')
            fd = os.open(MARKER_NAME, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
            finally:
                os.fsync(directory)
        if (not stat.S_ISREG(db_info.st_mode) or db_info.st_nlink != 1 or db_info.st_uid != os.getuid() or
                stat.S_IMODE(db_info.st_mode) != 0o600):
            raise StateError('production_state_file_invalid')
        marker_fd = os.open(MARKER_NAME, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        with os.fdopen(marker_fd, 'rb') as stream:
            marker_info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(marker_info.st_mode) or marker_info.st_nlink != 1 or
                    marker_info.st_uid != os.getuid() or stat.S_IMODE(marker_info.st_mode) != 0o600):
                raise StateError('production_state_marker_invalid')
            marker = json.loads(stream.read(1025))
        nonce = marker.get('nonce') if isinstance(marker, dict) else None
        if (not isinstance(nonce, str) or len(nonce) != 64 or any(c not in '0123456789abcdef' for c in nonce) or
                marker != {'version': 1, 'environment': 'production', 'device': db_info.st_dev,
                           'inode': db_info.st_ino, 'nonce': nonce}):
            raise StateError('production_state_binding_mismatch')
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
            db.execute('PRAGMA query_only=ON')
            rows = db.execute('SELECT nonce FROM production_state_binding').fetchall()
            if rows != [(nonce,)]:
                raise StateError('production_state_binding_mismatch')
    except (OSError, ValueError, TypeError, sqlite3.Error):
        raise StateError('production_state_binding_invalid') from None
    finally:
        os.close(directory)
