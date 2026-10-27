"""Owner DMs through a durable outbox (table `owner_outbox`, owned here).

Every message to the Owner (approval cards, held/error DMs, the 20:00 report) is first written to the outbox with a
fixed MTProto `random_id`, then sent. A resend after a crash or a Telegram failure reuses the stored `random_id`, so
Telegram deduplicates it, and the server message ids are stored once known. A failed send backs off and the row stays
queued. The Owner binding is checked by the sender itself (`telegram.make_owner_*_sender`).
"""

import asyncio
import hashlib
import inspect
import json
import logging
import time

LOG = logging.getLogger(__name__)
VERSION = 1
MAX_TEXT = 3500
BACKOFF_BASE = 300
BACKOFF_MAX = 3600
MAX_CAPTION = 4096
SEND_TIMEOUT_SECONDS = 30   # a hung Telegram send counts as a failed send (backoff), it must never stall the scheduler


class OutboxBackoff(RuntimeError):
    """The queued message is waiting out its backoff; nothing was sent."""


def notice_key(kind, ref, version=VERSION):
    return hashlib.sha256(json.dumps([version, kind, str(ref)], ensure_ascii=False).encode()).hexdigest()


def uncertain_notice(row):
    """DM for a post whose publish outcome is unknown. Carries ids and slot only, never draft content."""
    if row.get("state") != "uncertain":
        return None
    key = notice_key("uncertain", f"{row['id']}|{row['slot_at']}|{row.get('reason', '')}")
    return key, (f"NailBlog: bài #{row['id']} (slot {row['slot_at']}) chưa xác định được kết quả đăng. "
                 "Kiểm tra trang quản trị, không đăng lại. Dùng /nailblog hold <id> <lý do> nếu muốn giữ.")


def random_id_for(key):
    """Stable positive 63-bit id derived from the outbox key (the stored value stays authoritative)."""
    value = int.from_bytes(hashlib.sha256(('nailblog-outbox|' + key).encode()).digest()[:8], 'big') & ((1 << 63) - 1)
    return value or 1


CARD_BACKOFF_MAX = 300   # approval cards stay retryable every 5 minutes: their deadline is short


def backoff_seconds(attempts, cap=BACKOFF_MAX):
    return min(BACKOFF_BASE * 2 ** max(0, attempts - 1), cap)


def message_ids(result):
    """Server message ids out of what a sender returned: ids, a message, a list, or Telethon updates."""
    if result is None or isinstance(result, bool):
        return []
    if isinstance(result, int):
        return [result] if result > 0 else []
    if isinstance(result, (list, tuple)):
        found = []
        for item in result:
            found += message_ids(item)
        return found
    if isinstance(result, dict):
        return message_ids(result.get('message_ids'))
    ident = getattr(result, 'id', None)
    if type(ident) is int and ident > 0:
        return [ident]
    found = []
    for update in list(getattr(result, 'updates', None) or ()) + [getattr(result, 'update', None)]:
        if update is None:
            continue
        if type(getattr(update, 'id', None)) is int and update.id > 0 and type(update).__name__ == 'UpdateMessageID':
            found.append(update.id)
        inner = getattr(update, 'message', None)
        if type(getattr(inner, 'id', None)) is int and inner.id > 0:
            found.append(inner.id)
    return sorted(set(found))


def _wants_random_id(send):
    try:
        params = inspect.signature(send).parameters
    except (TypeError, ValueError):
        return False
    return 'random_id' in params   # explicit only: a `**kwargs`/Mock sender keeps the legacy `send(text)` call


class Outbox:
    def __init__(self, state):
        self.state = state
        with state.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS owner_outbox(
                    key TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('text','media')),
                    text TEXT NOT NULL, random_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','sent')),
                    message_ids TEXT NOT NULL DEFAULT '[]', album_ids TEXT NOT NULL DEFAULT '[]',
                    attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at REAL NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL, sent_at REAL NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS owner_outbox_files(
                    key TEXT NOT NULL, idx INTEGER NOT NULL, data BLOB NOT NULL, PRIMARY KEY(key, idx));
            ''')

    def get(self, key):
        with self.state.connection() as db:
            row = db.execute('SELECT * FROM owner_outbox WHERE key=?', (key,)).fetchone()
            return dict(row) if row else None

    def _legacy_sent(self, key):
        with self.state.connection() as db:
            row = db.execute('SELECT sent_at FROM notices WHERE key=?', (key,)).fetchone()
            return bool(row and row['sent_at'])

    def enqueue(self, key, text, now, *, kind='text', files=()):
        """Idempotent: an existing key keeps its text, files and `random_id`."""
        with self.state.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM owner_outbox WHERE key=?', (key,)).fetchone() is None:
                db.execute('INSERT INTO owner_outbox(key,kind,text,random_id,created_at) VALUES(?,?,?,?,?)',
                           (key, kind, text, random_id_for(key), now))
                for index, data in enumerate(files):
                    db.execute('INSERT INTO owner_outbox_files(key,idx,data) VALUES(?,?,?)', (key, index, data))
        return self.get(key)

    def files(self, key):
        with self.state.connection() as db:
            return [bytes(r['data']) for r in db.execute(
                'SELECT data FROM owner_outbox_files WHERE key=? ORDER BY idx', (key,))]

    def begin_attempt(self, key):
        """Persisted BEFORE the send: a crash mid-send leaves attempts>0 and the same random_id."""
        with self.state.connection() as db:
            db.execute('UPDATE owner_outbox SET attempts=attempts+1 WHERE key=? AND status=?', (key, 'pending'))

    def failed(self, key, now, error, *, album_ids=None, cap=BACKOFF_MAX):
        row = self.get(key)
        with self.state.connection() as db:
            db.execute("UPDATE owner_outbox SET next_attempt_at=?, last_error=?, album_ids=COALESCE(?,album_ids) "
                       "WHERE key=? AND status='pending'",
                       (now + backoff_seconds(row['attempts'] if row else 1, cap), type(error).__name__,
                        json.dumps(album_ids) if album_ids else None, key))

    def sent(self, key, now, ids):
        with self.state.connection() as db:
            db.execute("UPDATE owner_outbox SET status='sent', sent_at=?, message_ids=?, next_attempt_at=0, "
                       "last_error='' WHERE key=?", (now, json.dumps(list(ids)), key))
            db.execute('DELETE FROM owner_outbox_files WHERE key=?', (key,))

    def pending(self):
        with self.state.connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM owner_outbox WHERE status='pending' ORDER BY created_at, key")]

    def pending_count(self):
        with self.state.connection() as db:
            return db.execute("SELECT COUNT(*) FROM owner_outbox WHERE status='pending'").fetchone()[0]


async def send_once(state, send, key, text, now=None):
    """Send `text` through the outbox unless `key` was already delivered or is backing off; True when delivered now.

    `send(text)` is the legacy sender; a sender that accepts `random_id` gets the stored fixed one."""
    now = time.time() if now is None else now
    box = Outbox(state)
    if box.get(key) is None and box._legacy_sent(key):
        return False   # delivered before the outbox existed
    row = box.enqueue(key, text[:MAX_TEXT], now)
    if row['status'] == 'sent' or row['next_attempt_at'] > now:
        return False
    box.begin_attempt(key)
    try:
        if _wants_random_id(send):
            result = await asyncio.wait_for(send(row['text'], random_id=row['random_id']), SEND_TIMEOUT_SECONDS)
        else:
            result = await asyncio.wait_for(send(row['text']), SEND_TIMEOUT_SECONDS)
    except Exception as error:
        box.failed(key, now, error)
        state.notice_failed(key, now)
        LOG.warning("NailBlog notice send failed exception=%s", type(error).__name__)
        return False
    box.sent(key, now, message_ids(result))
    state.notice_sent(key, now)
    return True


def media_key(owner_id, files, caption):
    digest = hashlib.sha256()
    for data in files:
        digest.update(hashlib.sha256(data).digest())
    return notice_key('media', f'{owner_id}|{digest.hexdigest()}|{caption}')


def outbox_media_sender(state, send, *, clock=time.time):
    """Wrap `telegram.make_owner_media_sender(...)` so every card is queued durably with a fixed `random_id`.

    Same call shape as the raw sender: `await sender(owner_id, files, caption)` -> list of server message ids.
    An identical card already delivered returns its stored ids without sending; a card in backoff raises
    OutboxBackoff; a partly delivered album is not sent twice (its ids are kept and the retry sends the caption)."""
    box = Outbox(state)

    async def sender(owner_id, files, caption_text):
        blobs = []
        for data in files:
            data = data.read() if hasattr(data, 'read') else data
            blobs.append(bytes(data))
        now = clock()
        key = media_key(owner_id, blobs, caption_text)
        row = box.enqueue(key, caption_text[:MAX_CAPTION], now, kind='media', files=blobs)
        if row['status'] == 'sent':
            return json.loads(row['message_ids'])
        if row['next_attempt_at'] > now:
            raise OutboxBackoff('owner outbox message is backing off')
        album = json.loads(row['album_ids'])
        box.begin_attempt(key)
        try:
            ids = await asyncio.wait_for(send(owner_id, box.files(key) if not album else [], row['text'],
                                              random_id=row['random_id'], skip_files=bool(album)),
                                         SEND_TIMEOUT_SECONDS)
        except Exception as error:
            box.failed(key, now, error, album_ids=list(getattr(error, 'sent_ids', None) or []) or None,
                       cap=CARD_BACKOFF_MAX)
            raise
        total = album + [i for i in message_ids(ids) if i not in album]
        box.sent(key, now, total)
        return total
    return sender


async def deliver_pending(state, send, now=None):
    """Retry every queued text that is due (media cards are retried by their engine). Returns how many were sent."""
    now = time.time() if now is None else now
    sent = 0
    for row in Outbox(state).pending():
        if row['kind'] == 'text' and row['next_attempt_at'] <= now:
            sent += await send_once(state, send, row['key'], row['text'], now=now)
    return sent
