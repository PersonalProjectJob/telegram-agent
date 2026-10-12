"""One durable coalesced queue, with per-post checkpoints and conservative recovery."""

import json
import os
import sqlite3
import stat
from contextlib import contextmanager
from pathlib import Path
from .validation import NeedsHuman


class State:
    def __init__(self, path):
        self.path = str(path)
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if target.is_symlink():
            raise NeedsHuman('state database must not be a symlink')
        if os.name == 'posix':
            info = target.parent.stat()
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise NeedsHuman('state directory requires service ownership and mode 0700')
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS control(id INTEGER PRIMARY KEY CHECK(id=1),
                    paused INTEGER NOT NULL, pending INTEGER NOT NULL, running INTEGER NOT NULL,
                    last_result TEXT NOT NULL);
                INSERT OR IGNORE INTO control VALUES(1,0,0,0,'not scanned');
                CREATE TABLE IF NOT EXISTS posts(id TEXT PRIMARY KEY, hash TEXT NOT NULL,
                    state TEXT NOT NULL, original TEXT NOT NULL, expected TEXT NOT NULL,
                    reason TEXT NOT NULL);
            """)
        os.chmod(self.path, 0o600)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def recover_queue(self):
        with self.connection() as db:
            db.execute("UPDATE control SET pending=MAX(pending,running),running=0 WHERE id=1")
            # No mutation yet, so safe to analyze again after crash during read-only extraction.
            db.execute("UPDATE posts SET state='queued' WHERE state='analyzing'")

    def enqueue(self):
        with self.connection() as db:
            return db.execute("UPDATE control SET pending=1 WHERE id=1 AND pending=0 AND running=0").rowcount == 1

    def claim_scan(self):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            return db.execute("UPDATE control SET running=1,pending=0 WHERE id=1 AND paused=0 AND pending=1 AND running=0").rowcount == 1

    def finish_scan(self, result):
        with self.connection() as db:
            db.execute("UPDATE control SET running=0,last_result=? WHERE id=1", (result[:200],))

    def pause(self, value):
        with self.connection() as db:
            db.execute("UPDATE control SET paused=? WHERE id=1", (int(value),))

    def status(self):
        with self.connection() as db:
            result = dict(db.execute("SELECT * FROM control WHERE id=1").fetchone())
            result['counts'] = dict(db.execute("SELECT state,COUNT(*) FROM posts GROUP BY state"))
            return result

    def observe(self, post_id, fingerprint, original, *, requeue_preview=False):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
            if old and old['state'] == 'preview' and requeue_preview:
                db.execute("UPDATE posts SET hash=?,state='queued',original=?,expected='{}',reason='' WHERE id=?",
                           (fingerprint, json.dumps(original, ensure_ascii=False), post_id))
                return True
            if old and (old['hash'] == fingerprint or old['state'] in ('before_save', 'before_approve', 'uncertain', 'approved')):
                return bool(old['state'] == 'queued')
            db.execute("INSERT OR REPLACE INTO posts VALUES(?,?,'queued',?,'{}','')",
                       (post_id, fingerprint, json.dumps(original, ensure_ascii=False)))
            return True

    def claim_post(self, post_id):
        with self.connection() as db:
            return db.execute("UPDATE posts SET state='analyzing' WHERE id=? AND state='queued'", (post_id,)).rowcount == 1

    def mark(self, post_id, state, reason='', expected=None):
        with self.connection() as db:
            if expected is None:
                db.execute("UPDATE posts SET state=?,reason=? WHERE id=?", (state, reason[:300], post_id))
            else:
                db.execute("UPDATE posts SET state=?,reason=?,expected=? WHERE id=?",
                           (state, reason[:300], json.dumps(expected, ensure_ascii=False), post_id))

    def get(self, post_id):
        with self.connection() as db:
            row = db.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
            return dict(row) if row else None

    def interrupted(self):
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM posts WHERE state IN ('before_save','before_approve')")]
