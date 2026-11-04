"""Opt-in, separate one-shot worker for official review; no publication access."""
import argparse
from contextlib import contextmanager, closing
import json
import os
from pathlib import Path
import sqlite3
from datetime import datetime, time
from zoneinfo import ZoneInfo

from .official_sources import SOURCES
from .official_draft import create, source_snapshot
from .localization import QualityHold
from .review_client import submit
from .agy_broker import call


@contextmanager
def review_lock(root):
    path = Path(root) / 'locks' / 'official-review.lock'
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink(): raise ValueError('invalid review lock')
    with path.open('a+b') as stream:
        if os.name == 'nt':
            import msvcrt
            stream.seek(0); stream.write(b'0'); stream.flush(); stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def once(config, draft=create, send=submit, clock=None):
    """Process at most two new snapshots; each failure requires manual review.

    One automatic attempt per snapshot is the bounded retry policy. A crash
    during generation is held; a crash or timeout during send is unknown.
    """
    options = config.get('official_review', {})
    if not isinstance(options, dict) or options.get('enabled') is not True:
        return []
    clock = clock or (lambda: datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')))
    def local_time():
        current = clock()
        if current.tzinfo is None: raise ValueError('timezone-aware clock required')
        return current.astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).time().replace(tzinfo=None)
    if not time(5,30) <= local_time() < time(5,40):
        return []
    def inference(prompt):
        # A broker request can occupy 220s after the caller connects. This cutoff
        # leaves more than two hours before the 08:00 scheduled publication slot.
        if not time(5,30) <= local_time() < time(5,50):
            raise ValueError('official inference window closed')
        return call(config['localization']['socket_path'], prompt)
    root = Path(config['data_root'])
    socket_path = options.get('socket_path')
    if (not root.is_absolute() or root != root.resolve() or not isinstance(socket_path, str)
            or not Path(socket_path).is_absolute() or Path(socket_path) != Path(socket_path).resolve()):
        raise ValueError('invalid review worker paths')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    dbpath = root / 'official-review-worker.sqlite3'
    if dbpath.is_symlink(): raise ValueError('invalid review worker database')
    results = []
    with review_lock(root), closing(sqlite3.connect(dbpath, isolation_level=None)) as db:
        db.execute('PRAGMA journal_mode=WAL'); db.execute('PRAGMA synchronous=FULL')
        db.execute('''CREATE TABLE IF NOT EXISTS jobs (source_id TEXT NOT NULL, source_sha TEXT NOT NULL,
                   state TEXT NOT NULL, artifact_sha TEXT, reason TEXT, PRIMARY KEY(source_id,source_sha))''')
        db.execute('CREATE TABLE IF NOT EXISTS events (source_id TEXT NOT NULL, source_sha TEXT NOT NULL, event TEXT NOT NULL, created_at TEXT NOT NULL)')
        db.execute("UPDATE jobs SET state='held',reason='interrupted_generation' WHERE state='generating'")
        db.execute("UPDATE jobs SET state='unknown',reason='interrupted_send' WHERE state='sending'")
        for item in SOURCES:
            directory = root / 'official-review-queue' / item['id']
            if directory.is_symlink() or not directory.is_dir(): continue
            for path in sorted(directory.iterdir(), key=lambda p: p.name):
                name = path.name
                if path.is_symlink() or not path.is_dir() or len(name) != 64 or any(c not in '0123456789abcdef' for c in name): continue
                if not time(5,30) <= local_time() < time(5,50): return results
                inserted = db.execute("INSERT OR IGNORE INTO jobs VALUES (?,?,'generating',NULL,NULL)", (item['id'], name))
                if not inserted.rowcount:
                    changed = db.execute("UPDATE jobs SET state='generating',reason=NULL WHERE source_id=? AND source_sha=? AND state='retry_ready'", (item['id'],name))
                    if not changed.rowcount: continue
                try:
                    if not time(5,30) <= local_time() < time(5,50):
                        raise ValueError('official review window closed')
                    result = draft(root, item['id'], name, config, inference=inference)
                    artifact_path = Path(result['path'])
                    if result.get('status') != 'HOLD' or artifact_path.is_symlink() or artifact_path.parent != root/'official-drafts'/item['id']/name:
                        raise ValueError('invalid draft result')
                    artifact = json.loads(artifact_path.read_text(encoding='utf-8'))
                    if artifact.get('source') != source_snapshot(root, item['id'], name):
                        raise ValueError('source changed after drafting')
                    db.execute("UPDATE jobs SET state='sending',artifact_sha=? WHERE source_id=? AND source_sha=? AND state='generating'", (result['sha256'], item['id'], name))
                except Exception as exc:
                    reason = 'quality_hold' if isinstance(exc, QualityHold) else 'inference_or_source_unavailable'
                    db.execute("UPDATE jobs SET state='held',reason=? WHERE source_id=? AND source_sha=? AND state='generating'", (reason,item['id'], name))
                    results.append({'source': item['id'], 'state': 'held'})
                    if len(results) >= 2: return results
                    continue
                try:
                    reply = send(socket_path, str(artifact_path), result['sha256'])
                    if reply != {'state': 'pending', 'sha256': result['sha256']}:
                        raise ValueError('send not confirmed')
                except Exception:
                    db.execute("UPDATE jobs SET state='unknown',reason='send_unconfirmed' WHERE source_id=? AND source_sha=? AND state='sending'", (item['id'], name))
                    results.append({'source': item['id'], 'state': 'unknown'})
                else:
                    db.execute("UPDATE jobs SET state='submitted' WHERE source_id=? AND source_sha=? AND state='sending'", (item['id'], name))
                    results.append({'source': item['id'], 'state': 'submitted'})
                if len(results) >= 2: return results
    return results


def requeue_held(root, source_id, source_sha):
    root = Path(root)
    if not root.is_absolute() or root != root.resolve() or source_id not in {x['id'] for x in SOURCES} or len(source_sha) != 64 or any(c not in '0123456789abcdef' for c in source_sha):
        raise ValueError('invalid job identity')
    dbpath = root/'official-review-worker.sqlite3'
    if dbpath.is_symlink() or not dbpath.is_file(): raise ValueError('review state unavailable')
    with review_lock(root), closing(sqlite3.connect(dbpath, isolation_level=None)) as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            changed = db.execute("UPDATE jobs SET state='retry_ready' WHERE source_id=? AND source_sha=? AND state='held'", (source_id,source_sha))
            if changed.rowcount != 1: raise ValueError('only held jobs may be requeued')
            db.execute("INSERT INTO events VALUES (?,?,'manual_requeue',?)",(source_id,source_sha,datetime.now(ZoneInfo('UTC')).isoformat()))
            db.execute('COMMIT')
        except BaseException:
            db.execute('ROLLBACK'); raise


def status(root):
    root = Path(root); dbpath = root/'official-review-worker.sqlite3'
    if not root.is_absolute() or root != root.resolve() or dbpath.is_symlink() or not dbpath.is_file():
        raise ValueError('review state unavailable')
    with closing(sqlite3.connect(dbpath)) as db:
        return [dict(zip(('source_id','source_sha','state','reason'), row)) for row in db.execute('SELECT source_id,source_sha,state,reason FROM jobs ORDER BY source_id,source_sha')]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--requeue-held', nargs=2, metavar=('SOURCE_ID', 'SOURCE_SHA256'))
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding='utf-8'))
    if args.status and args.requeue_held: parser.error('choose one operation')
    if args.status: print(json.dumps(status(config['data_root'])))
    elif args.requeue_held:
        requeue_held(config['data_root'], *args.requeue_held)
        print(json.dumps({'state':'retry_ready'}))
    else: print(json.dumps(once(config)))


if __name__ == '__main__': main()
