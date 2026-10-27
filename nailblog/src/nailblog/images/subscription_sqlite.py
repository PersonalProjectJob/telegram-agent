"""Bounded stable DB/WAL capture; SQLite only opens a private, disposable copy.

No native SQLite connection, header patch, SHM copy, or generation retry. WAL
checksum/layout: https://www.sqlite.org/fileformat2.html#wal_file_format sections4.1-4.2.
Read-only copy permits local derived sidecars per https://www.sqlite.org/wal.html#read_only_databases.
Strict old/partial tail rejection is fail-closed intake, not a universal live backup.
"""
from contextlib import ExitStack, closing
import hashlib
import os
from pathlib import Path
import sqlite3
import struct
import tempfile
import time

from . import ImageError
from .subscription_io import Directory, valid_stat

MAX_BYTES = 32 * 1024 * 1024
TIMEOUT_SECONDS = 5
COLUMNS = ('idx','step_type','status','step_format','step_payload','permissions','error_details')


def _stamp(info):
    return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns,
            info.st_mode,info.st_uid,info.st_gid,info.st_nlink)


def _deadline(clock, until):
    if clock() >= until: raise ImageError('cli_sqlite_snapshot_deadline')


def _checksum(data, order, state=(0,0)):
    first,second=state
    for a,b in struct.iter_unpack(order+'II',data):
        first=(first+a+second)&0xffffffff
        second=(second+b+first)&0xffffffff
    return first,second


def _validate_wal(db, wal, *, clock, until):
    if len(db)<100 or db[:16]!=b'SQLite format 3\0' or db[18:20] not in (b'\1\1',b'\2\2'):
        raise ImageError('cli_sqlite_header')
    page=int.from_bytes(db[16:18],'big');page=65536 if page==1 else page
    if page<512 or page>65536 or page&(page-1) or len(db)%page:
        raise ImageError('cli_sqlite_header')
    if not wal:return
    if len(wal)<32 or db[18:20]!=b'\2\2':raise ImageError('cli_sqlite_wal_shape')
    magic,version,wal_page,_,salt1,salt2,check1,check2=struct.unpack('>8I',wal[:32])
    if magic not in (0x377f0682,0x377f0683) or version!=3007000 or wal_page!=page or (len(wal)-32)%(24+page):
        raise ImageError('cli_sqlite_wal_shape')
    order='<' if magic==0x377f0682 else '>'
    state=_checksum(wal[:24],order)
    if state!=(check1,check2):raise ImageError('cli_sqlite_wal_checksum')
    committed=0
    for offset in range(32,len(wal),24+page):
        _deadline(clock,until)
        number,committed,a,b,c,d=struct.unpack('>6I',wal[offset:offset+24])
        if not 0<number<=MAX_BYTES//page or committed>MAX_BYTES//page or (a,b)!=(salt1,salt2):
            raise ImageError('cli_sqlite_wal_shape')
        state=_checksum(wal[offset:offset+8],order,state)
        state=_checksum(memoryview(wal)[offset+24:offset+24+page],order,state)
        if state!=(c,d):raise ImageError('cli_sqlite_wal_checksum')
    # A partial/uncommitted/stale tail is held, not silently discarded. A header
    # without frames is harmless; a nonempty frame sequence must end at commit.
    if len(wal)>32 and not committed:raise ImageError('cli_sqlite_wal_uncommitted')


def _capture(directory, handles, stamps, *, owner, clock, until):
    def verify():
        for name,fd in handles.items():
            _deadline(clock,until)
            try:current=os.stat(directory._name(name),dir_fd=directory.fd,follow_symlinks=False)
            except FileNotFoundError:
                if fd is None:continue
                raise ImageError('cli_sqlite_snapshot_changed') from None
            if fd is None:raise ImageError('cli_sqlite_snapshot_changed')
            valid_stat(current,owner=owner,limit=MAX_BYTES,native=True)
            if _stamp(current)!=stamps[name][0] or _stamp(os.fstat(fd))!=stamps[name][1]:
                raise ImageError('cli_sqlite_snapshot_changed')
    verify();out={};total=0
    for name,fd in handles.items():
        if fd is None:out[name]=None;continue
        os.lseek(fd,0,os.SEEK_SET);data=bytearray()
        while True:
            _deadline(clock,until)
            block=os.read(fd,min(65536,MAX_BYTES-total+1))
            if not block:break
            total+=len(block)
            if total>MAX_BYTES:raise ImageError('cli_sqlite_snapshot_bounds')
            data.extend(block)
        out[name]=bytes(data)
    verify();return out


def capture(path, *, owner=None, clock=time.monotonic):
    """Exactly two full captures with held nofollow FDs and stable path identity."""
    path=Path(path);until=clock()+TIMEOUT_SECONDS
    with Directory(path.parent,owner=owner,native=True) as directory, ExitStack() as stack:
        handles={};stamps={}
        for name in (path.name,path.name+'-wal'):
            try:fd=os.open(directory._name(name),os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_BINARY',0),dir_fd=directory.fd)
            except FileNotFoundError:
                if name==path.name:raise ImageError('cli_sqlite_snapshot_missing') from None
                handles[name]=None;continue
            except OSError:raise ImageError('cli_sqlite_snapshot_path') from None
            stack.callback(os.close,fd)
            info=os.fstat(fd);valid_stat(info,owner=owner,limit=MAX_BYTES,native=True)
            current=os.stat(directory._name(name),dir_fd=directory.fd,follow_symlinks=False)
            valid_stat(current,owner=owner,limit=MAX_BYTES,native=True)
            path_stamp,fd_stamp=_stamp(current),_stamp(info)
            # Windows exposes distinct creation-time caches through stat/fstat
            # on an open SQLite writer. Track BOTH complete stamps for stability;
            # the POSIX worker additionally requires complete initial equality.
            if (path_stamp!=fd_stamp if os.name=='posix' else
                    path_stamp[:4]+path_stamp[5:]!=fd_stamp[:4]+fd_stamp[5:]):
                raise ImageError('cli_sqlite_snapshot_changed')
            handles[name]=fd;stamps[name]=(path_stamp,fd_stamp)
        if sum(stamp[0][2] for stamp in stamps.values())>MAX_BYTES:raise ImageError('cli_sqlite_snapshot_bounds')
        first=_capture(directory,handles,stamps,owner=owner,clock=clock,until=until)
        second=_capture(directory,handles,stamps,owner=owner,clock=clock,until=until)
        if first!=second:raise ImageError('cli_sqlite_snapshot_changed')
        db,wal=first[path.name],first[path.name+'-wal']
        _validate_wal(db,wal,clock=clock,until=until)
        _deadline(clock,until)
        return db,wal


def _query(path, *, clock, until):
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=0)) as db:
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,2*1024*1024)
        db.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH,256*1024)
        db.setlimit(sqlite3.SQLITE_LIMIT_COLUMN,32)
        db.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED,0)
        db.set_progress_handler(lambda: int(clock()>=until),1000)
        db.execute('PRAGMA query_only=ON');db.execute('PRAGMA trusted_schema=OFF')
        if db.execute('PRAGMA quick_check(1)').fetchone()!=('ok',):raise ImageError('cli_sqlite_integrity')
        if db.execute("SELECT type FROM sqlite_schema WHERE name='steps'").fetchall()!=[('table',)]:
            raise ImageError('cli_sqlite_steps_schema')
        def authorize(action,table,column,*_):
            if action==sqlite3.SQLITE_SELECT:return sqlite3.SQLITE_OK
            if action==sqlite3.SQLITE_READ and table=='steps' and column in COLUMNS:return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        db.set_authorizer(authorize)
        rows=db.execute('SELECT '+','.join(COLUMNS)+' FROM steps ORDER BY idx LIMIT 65').fetchall()
        _deadline(clock,until)
        if len(rows)>64:raise ImageError('cli_native_step_bounds')
        return rows


def snapshot_rows(path, private, *, owner=None, clock=time.monotonic):
    """SHM is derived by SQLite only in this worker-private disposable directory."""
    db,wal=capture(path,owner=owner,clock=clock)
    until=clock()+TIMEOUT_SECONDS
    try:
        with tempfile.TemporaryDirectory(prefix='sqlite-snapshot-',dir=private) as temp:
            with Directory(temp,owner=owner) as directory:
                if os.name=='posix' and os.fstat(directory.fd).st_mode&0o077:
                    raise ImageError('cli_sqlite_private_snapshot_required')
                directory.atomic('conversation.db',db,raw=True)
                if wal is not None:directory.atomic('conversation.db-wal',wal,raw=True)
            rows=_query(Path(temp)/'conversation.db',clock=clock,until=until)
        return rows,{'method':'private-stable-db-wal-v1','db_sha':hashlib.sha256(db).hexdigest(),
                     'wal_sha':None if wal is None else hashlib.sha256(wal).hexdigest(),
                     'db_bytes':len(db),'wal_bytes':None if wal is None else len(wal)}
    except sqlite3.Error:
        raise ImageError('cli_sqlite_snapshot_unreadable') from None
