"""Durable SQLite (WAL) state: control, topics, posts, images, runs, usage, notices."""

import json
import os
import sqlite3
import stat
from contextlib import contextmanager, nullcontext
from datetime import datetime
from pathlib import Path


POST_STATES = ("writing", "held", "review", "scheduled", "before_publish", "published", "uncertain")
# Allowed post transitions (spec section 4). Anything else is a bug or a stale command.
TRANSITIONS = {
    "writing": ("held", "review"),
    "held": ("writing",),
    "review": ("writing", "scheduled", "held"),
    "scheduled": ("before_publish", "held"),
    "before_publish": ("published", "uncertain"),
    "uncertain": ("published", "held"),
    "published": (),
}
MOVABLE_STATES = ("writing", "held", "review", "scheduled")
TOPIC_STATES = ("proposed", "approved", "dropped")


class InvalidTransition(ValueError):
    """The requested post state change is not an edge of the lifecycle graph."""


class EvidenceRequired(InvalidTransition):
    """Moving a post to `published` from before_publish/uncertain needs non-empty readback evidence."""


class StateError(ValueError):
    """The state directory or database fails a safety check, or a row cannot be changed."""


class SlotTaken(StateError):
    """Another topic is already `approved` for that slot: a slot holds at most one approved topic."""

    def __init__(self, slot_at, holder_id):
        self.slot_at, self.holder_id = slot_at, holder_id
        super().__init__(f"slot {slot_at} already has approved topic #{holder_id}")


def _slot_text(value):
    """A slot must be an ISO datetime that carries a timezone; anything else is refused."""
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise StateError("slot_at must be an ISO datetime with a timezone") from None
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise StateError("slot_at must be an ISO datetime with a timezone")
    return value


def _content(value):
    """Real content: a non-blank string, or a dict/list holding some. Numbers, booleans and None are never content."""
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(_content(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_content(item) for item in value)
    return False


def _has_evidence(value):
    """Readback evidence is a non-blank string, or a dict whose "readback" is a non-blank string or a dict/list
    that holds some. A number or a boolean is not evidence."""
    if isinstance(value, str):
        return bool(value.strip())
    return isinstance(value, dict) and _content(value.get("readback"))


def _claim_slot(db, topic_id, slot_at):
    """THE one place that keeps `at most one approved topic per slot`: raise SlotTaken when a topic other than
    `topic_id` is approved for `slot_at`. Call it inside the transaction that approves or moves a topic."""
    if slot_at is None:
        return
    row = db.execute("SELECT id FROM topics WHERE state='approved' AND slot_at=? AND id<>? ORDER BY id LIMIT 1",
                     (slot_at, topic_id)).fetchone()
    if row is not None:
        raise SlotTaken(slot_at, row["id"])


class State:
    def __init__(self, path, *, image_mode="fixed"):
        self.path = str(path)
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if target.is_symlink():
            raise StateError("state database must not be a symlink")
        if os.name == "posix":
            info = target.parent.stat()
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise StateError("state directory requires service ownership and mode 0700")
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS control(id INTEGER PRIMARY KEY CHECK(id=1),
                    paused INTEGER NOT NULL, image_mode TEXT NOT NULL, last_result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS topics(id INTEGER PRIMARY KEY AUTOINCREMENT, week TEXT NOT NULL,
                    title TEXT NOT NULL, category TEXT NOT NULL, slot_at TEXT, score REAL NOT NULL DEFAULT 0,
                    sources_json TEXT NOT NULL DEFAULT '[]', state TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS posts(id INTEGER PRIMARY KEY AUTOINCREMENT, topic_id INTEGER,
                    slot_at TEXT NOT NULL, state TEXT NOT NULL, draft_json TEXT NOT NULL DEFAULT '{}',
                    fingerprint TEXT NOT NULL DEFAULT '', admin_id TEXT NOT NULL DEFAULT '',
                    reason TEXT NOT NULL DEFAULT '', details TEXT NOT NULL DEFAULT '{}');
                CREATE TABLE IF NOT EXISTS images(id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER NOT NULL,
                    position INTEGER NOT NULL, prompt TEXT NOT NULL DEFAULT '', round INTEGER NOT NULL DEFAULT 1,
                    path TEXT NOT NULL DEFAULT '', sha256 TEXT NOT NULL DEFAULT '', dhash TEXT NOT NULL DEFAULT '',
                    scores_json TEXT NOT NULL DEFAULT '{}', chosen INTEGER NOT NULL DEFAULT 0,
                    uploaded_url TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER NOT NULL,
                    mode TEXT NOT NULL, seed TEXT NOT NULL DEFAULT '', planned_at TEXT, started_at TEXT,
                    ended_at TEXT, n_generated INTEGER NOT NULL DEFAULT 0, n_pass_first INTEGER NOT NULL DEFAULT 0,
                    rounds INTEGER NOT NULL DEFAULT 0, n_fallback INTEGER NOT NULL DEFAULT 0,
                    usd REAL NOT NULL DEFAULT 0, met_deadline INTEGER);
                CREATE TABLE IF NOT EXISTS usage(at REAL NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
                    units REAL NOT NULL, usd REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS usage_at ON usage(at);
                CREATE TABLE IF NOT EXISTS notices(key TEXT PRIMARY KEY, sent_at REAL NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS trend_snapshots(id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fetched_at REAL NOT NULL, source TEXT NOT NULL, term TEXT NOT NULL,
                    traffic INTEGER NOT NULL DEFAULT 0, news_json TEXT NOT NULL DEFAULT '[]');
                CREATE INDEX IF NOT EXISTS trend_snapshots_at ON trend_snapshots(fetched_at);
                CREATE TABLE IF NOT EXISTS tiktok_tags(id INTEGER PRIMARY KEY AUTOINCREMENT,
                    added_at REAL NOT NULL, tag TEXT NOT NULL, views INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS daily_runs(day TEXT NOT NULL, step TEXT NOT NULL, done_at REAL NOT NULL,
                    message TEXT NOT NULL DEFAULT '', PRIMARY KEY(day, step));
            """)
            if image_mode not in ("fixed", "random", "adaptive"):
                raise StateError("image_mode must be fixed, random or adaptive")
            db.execute("INSERT OR IGNORE INTO control VALUES(1,0,?,'not started')", (image_mode,))
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

    # control
    def control(self):
        with self.connection() as db:
            return dict(db.execute("SELECT * FROM control WHERE id=1").fetchone())

    def set_paused(self, value):
        with self.connection() as db:
            db.execute("UPDATE control SET paused=? WHERE id=1", (int(bool(value)),))

    def set_image_mode(self, mode):
        if mode not in ("fixed", "random", "adaptive"):
            raise StateError("unknown image mode")
        with self.connection() as db:
            db.execute("UPDATE control SET image_mode=? WHERE id=1", (mode,))

    def set_last_result(self, text):
        with self.connection() as db:
            db.execute("UPDATE control SET last_result=? WHERE id=1", (str(text)[:200],))

    def status(self):
        with self.connection() as db:
            result = dict(db.execute("SELECT * FROM control WHERE id=1").fetchone())
            result["posts"] = dict(db.execute("SELECT state,COUNT(*) FROM posts GROUP BY state"))
            result["topics"] = dict(db.execute("SELECT state,COUNT(*) FROM topics GROUP BY state"))
            return result

    # topics
    def add_topic(self, week, title, category, slot_at=None, score=0.0, sources=(), state="proposed"):
        if state not in TOPIC_STATES:
            raise StateError("unknown topic state")
        if slot_at is not None:
            _slot_text(slot_at)
        # `sources` is a list, or a dict {"sources": [...], ...} when the planner stores extra topic metadata.
        stored = sources if isinstance(sources, dict) else list(sources)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if state == "approved":
                _claim_slot(db, 0, slot_at)
            return db.execute(
                "INSERT INTO topics(week,title,category,slot_at,score,sources_json,state) VALUES(?,?,?,?,?,?,?)",
                (week, title, category, slot_at, float(score), json.dumps(stored, ensure_ascii=False), state)).lastrowid

    def get_topic(self, topic_id):
        with self.connection() as db:
            row = db.execute("SELECT * FROM topics WHERE id=?", (topic_id,)).fetchone()
            return dict(row) if row else None

    def list_topics(self, states=("proposed", "approved")):
        marks = ",".join("?" * len(states))
        with self.connection() as db:
            return [dict(row) for row in db.execute(
                f"SELECT * FROM topics WHERE state IN ({marks}) ORDER BY id", tuple(states))]

    def set_topic_state(self, topic_id, state, *, only_from=None):
        """Return True when a row changed; `only_from` guards against stale commands.

        Approving a topic whose slot already has another approved topic raises SlotTaken and changes nothing."""
        if state not in TOPIC_STATES:
            raise StateError("unknown topic state")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state, slot_at FROM topics WHERE id=?", (topic_id,)).fetchone()
            if row is None or (only_from and row["state"] not in only_from):
                return False
            if state == "approved":
                _claim_slot(db, topic_id, row["slot_at"])
            return db.execute("UPDATE topics SET state=? WHERE id=?", (state, topic_id)).rowcount == 1

    def edit_topic(self, topic_id, title):
        with self.connection() as db:
            return db.execute("UPDATE topics SET title=? WHERE id=? AND state IN ('proposed','approved')",
                              (title, topic_id)).rowcount == 1

    def topics_for(self, week, slot_at=None):
        """Every topic of a week label (any state), optionally of one slot, in id order."""
        sql, args = "SELECT * FROM topics WHERE week=?", [week]
        if slot_at is not None:
            sql += " AND slot_at=?"
            args.append(slot_at)
        with self.connection() as db:
            return [dict(row) for row in db.execute(sql + " ORDER BY id", args)]

    def update_topic_meta(self, topic_id, **fields):
        """Merge keys into the topic's sources_json object (a legacy plain list becomes {"sources": list})."""
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT sources_json FROM topics WHERE id=?", (topic_id,)).fetchone()
            if row is None:
                raise StateError("unknown topic")
            try:
                data = json.loads(row["sources_json"] or "{}")
            except ValueError:
                data = {}
            data = {"sources": data} if isinstance(data, list) else data
            data.update(fields)
            db.execute("UPDATE topics SET sources_json=? WHERE id=?", (json.dumps(data, ensure_ascii=False), topic_id))

    def set_topic_slot(self, topic_id, slot_at):
        """Move a topic to a slot; an approved topic cannot move onto a slot that has another approved topic."""
        _slot_text(slot_at)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM topics WHERE id=?", (topic_id,)).fetchone()
            if row is not None and row["state"] == "approved":
                _claim_slot(db, topic_id, slot_at)
            return db.execute("UPDATE topics SET slot_at=? WHERE id=?", (slot_at, topic_id)).rowcount == 1

    def slot_holder(self, slot_at, *, excluding=0):
        """Id of the approved topic that holds `slot_at` (other than `excluding`), else None."""
        with self.connection() as db:
            row = db.execute("SELECT id FROM topics WHERE state='approved' AND slot_at=? AND id<>? ORDER BY id LIMIT 1",
                             (slot_at, excluding)).fetchone()
            return row["id"] if row else None

    def choose_candidate(self, week, slot_at, ordinal):
        """Atomically approve candidate number `ordinal` (1-based, id order) of a slot and drop its siblings.

        Choosing again swaps the choice. Returns the chosen topic id, or None when there is no such candidate.
        Raises SlotTaken when an approved topic outside the candidates already holds the slot."""
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT id FROM topics WHERE week=? AND slot_at=? ORDER BY id", (week, slot_at)).fetchall()
            if not 1 <= ordinal <= len(rows):
                return None
            holder = db.execute("SELECT id FROM topics WHERE state='approved' AND slot_at=? AND week<>? ORDER BY id LIMIT 1",
                                (slot_at, week)).fetchone()
            if holder is not None:
                raise SlotTaken(slot_at, holder["id"])
            for position, row in enumerate(rows, 1):
                db.execute("UPDATE topics SET state=? WHERE id=?", ("approved" if position == ordinal else "dropped",
                                                                    row["id"]))
            return rows[ordinal - 1]["id"]

    # posts
    def add_post(self, slot_at, *, topic_id=None, state="writing", draft=None, fingerprint=""):
        if state not in POST_STATES:
            raise StateError("unknown post state")
        _slot_text(slot_at)
        with self.connection() as db:
            return db.execute(
                "INSERT INTO posts(topic_id,slot_at,state,draft_json,fingerprint) VALUES(?,?,?,?,?)",
                (topic_id, slot_at, state, json.dumps(draft or {}, ensure_ascii=False), fingerprint)).lastrowid

    def get_post(self, post_id):
        with self.connection() as db:
            row = db.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
            return dict(row) if row else None

    def set_post_document(self, post_id, document):
        """Materialize a reviewed local article before its durable Create intent exists."""
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT state,admin_id FROM posts WHERE id=?', (post_id,)).fetchone()
            if not row or row['state'] != 'writing' or row['admin_id']:
                raise StateError('post document is immutable after Draft')
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='admin_operations'").fetchone():
                if db.execute('SELECT 1 FROM admin_operations WHERE post_id=?', (post_id,)).fetchone():
                    raise StateError('post document has a durable Create intent')
            db.execute('UPDATE posts SET draft_json=? WHERE id=?', (json.dumps(document, ensure_ascii=False), post_id))

    def posts_for_topic(self, topic_id):
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM posts WHERE topic_id=? ORDER BY id", (topic_id,))]

    def list_posts(self, states=None, *, slot_from=None, slot_to=None):
        sql, args = "SELECT * FROM posts WHERE 1=1", []
        if states:
            sql += " AND state IN (" + ",".join("?" * len(states)) + ")"
            args += list(states)
        if slot_from:
            sql += " AND slot_at>=?"
            args.append(slot_from)
        if slot_to:
            sql += " AND slot_at<?"
            args.append(slot_to)
        with self.connection() as db:
            return [dict(row) for row in db.execute(sql + " ORDER BY slot_at,id", args)]

    def transition(self, post_id, new_state, reason="", details=None, *, evidence=None, _db=None):
        """Move a post along the lifecycle graph atomically; raise InvalidTransition otherwise.

        `published` reached from before_publish/uncertain needs non-empty readback `evidence`,
        stored under details["evidence"]."""
        with (self.connection() if _db is None else nullcontext(_db)) as db:
            if _db is None:
                db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM posts WHERE id=?", (post_id,)).fetchone()
            if row is None:
                raise InvalidTransition("unknown post")
            if new_state not in TRANSITIONS.get(row["state"], ()):
                raise InvalidTransition(f"{row['state']} -> {new_state} is not allowed")
            if new_state == "published":
                if not _has_evidence(evidence):
                    raise EvidenceRequired("published needs non-empty readback evidence")
                details = {**(details or {}), "evidence": evidence}
            db.execute("UPDATE posts SET state=?,reason=?,details=? WHERE id=?",
                       (new_state, str(reason)[:300], json.dumps(details or {}, ensure_ascii=False, sort_keys=True),
                        post_id))

    def move_post(self, post_id, slot_at):
        """Change the slot of a post that has not started publishing."""
        _slot_text(slot_at)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM posts WHERE id=?", (post_id,)).fetchone()
            if row is None:
                raise InvalidTransition("unknown post")
            if row["state"] not in MOVABLE_STATES:
                raise InvalidTransition(f"a {row['state']} post cannot be moved")
            db.execute("UPDATE posts SET slot_at=? WHERE id=?", (slot_at, post_id))

    # usage
    def add_usage(self, at, provider, model, units, usd):
        with self.connection() as db:
            db.execute("INSERT INTO usage(at,provider,model,units,usd) VALUES(?,?,?,?,?)",
                       (float(at), provider, model, float(units), float(usd)))

    def usage_between(self, start, end):
        with self.connection() as db:
            return [tuple(row) for row in db.execute(
                "SELECT at,provider,model,units,usd FROM usage WHERE at>=? AND at<? ORDER BY at", (start, end))]

    def usd_between(self, start, end):
        with self.connection() as db:
            return float(db.execute("SELECT COALESCE(SUM(usd),0) FROM usage WHERE at>=? AND at<?",
                                    (start, end)).fetchone()[0])

    def prune_usage(self, before):
        with self.connection() as db:
            return db.execute("DELETE FROM usage WHERE at<?", (before,)).rowcount

    # trends: Google Trending snapshots, saved beauty-RSS items (source "rss:<host>") and hand-entered TikTok tags
    def add_trend_snapshots(self, fetched_at, source, rows, *, skip_known=False):
        """Store rows (term, traffic, news list). `skip_known` leaves out a (source, term) that is already stored."""
        added = 0
        with self.connection() as db:
            for term, traffic, news in rows:
                if skip_known and db.execute("SELECT 1 FROM trend_snapshots WHERE source=? AND term=? LIMIT 1",
                                             (source, term)).fetchone():
                    continue
                db.execute("INSERT INTO trend_snapshots(fetched_at,source,term,traffic,news_json) VALUES(?,?,?,?,?)",
                           (float(fetched_at), source, term, int(traffic), json.dumps(news, ensure_ascii=False)))
                added += 1
        return added

    def trend_snapshots(self, since, until=None):
        sql, args = "SELECT fetched_at,source,term,traffic,news_json FROM trend_snapshots WHERE fetched_at>=?", [since]
        if until is not None:
            sql += " AND fetched_at<?"
            args.append(until)
        with self.connection() as db:
            rows = []
            for row in db.execute(sql + " ORDER BY fetched_at,id", args):
                try:
                    news = json.loads(row["news_json"])
                except ValueError:
                    news = []
                rows.append({"fetched_at": row["fetched_at"], "source": row["source"], "term": row["term"],
                             "traffic": row["traffic"], "news": news if isinstance(news, list) else []})
            return rows

    def prune_trend_snapshots(self, before):
        with self.connection() as db:
            return db.execute("DELETE FROM trend_snapshots WHERE fetched_at<?", (before,)).rowcount

    def add_tiktok_tag(self, added_at, tag, views):
        with self.connection() as db:
            return db.execute("INSERT INTO tiktok_tags(added_at,tag,views) VALUES(?,?,?)",
                              (float(added_at), tag, int(views))).lastrowid

    def tiktok_tags(self, since=0.0):
        with self.connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT added_at,tag,views FROM tiktok_tags WHERE added_at>=? ORDER BY id", (since,))]

    # daily jobs: one row per (day, step) once its work is done; `message` is the Owner DM still to be delivered
    def daily_get(self, day, step):
        with self.connection() as db:
            row = db.execute("SELECT * FROM daily_runs WHERE day=? AND step=?", (day, step)).fetchone()
            return dict(row) if row else None

    def daily_mark(self, day, step, now, message=""):
        """Record a finished step; True only for the call that created the row (a repeat changes nothing)."""
        with self.connection() as db:
            return db.execute("INSERT OR IGNORE INTO daily_runs(day,step,done_at,message) VALUES(?,?,?,?)",
                              (day, step, float(now), message)).rowcount == 1

    def daily_rows(self, day):
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM daily_runs WHERE day=? ORDER BY done_at", (day,))]

    # notices (duplicate-DM guard)
    def notice_due(self, key, now):
        with self.connection() as db:
            row = db.execute("SELECT * FROM notices WHERE key=?", (key,)).fetchone()
            return row is None or (not row["sent_at"] and row["next_attempt_at"] <= now)

    def notice_sent(self, key, now):
        with self.connection() as db:
            db.execute("INSERT INTO notices(key,sent_at,attempts,next_attempt_at) VALUES(?,?,1,0) "
                       "ON CONFLICT(key) DO UPDATE SET sent_at=excluded.sent_at,attempts=attempts+1,next_attempt_at=0",
                       (key, now))

    def notice_failed(self, key, now, retry_seconds=300):
        with self.connection() as db:
            db.execute("INSERT INTO notices(key,sent_at,attempts,next_attempt_at) VALUES(?,0,1,?) "
                       "ON CONFLICT(key) DO UPDATE SET attempts=attempts+1,next_attempt_at=excluded.next_attempt_at "
                       "WHERE notices.sent_at=0", (key, now + retry_seconds))
