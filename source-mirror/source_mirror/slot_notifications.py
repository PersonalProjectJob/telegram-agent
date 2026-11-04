"""Durable scheduled-slot outcomes; never emits a Telegram message itself."""
import json
import re


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS slot_runs (
        day TEXT NOT NULL, slot TEXT NOT NULL, state TEXT NOT NULL,
        PRIMARY KEY(day,slot))''')
    db.execute('''CREATE TABLE IF NOT EXISTS slot_notice_events (
        event_id INTEGER PRIMARY KEY,
        day TEXT NOT NULL, slot TEXT NOT NULL, outcome TEXT NOT NULL,
        payload TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0)''')


def begin(store, day, slot):
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',day) or not re.fullmatch(r'\d{2}:\d{2}',slot): raise ValueError('invalid slot identity')
    db=store.db;schema(db)
    db.execute("INSERT INTO slot_runs(day,slot,state) VALUES (?,?,'running') ON CONFLICT(day,slot) DO UPDATE SET state='running'",(day,slot))


def claim_begin(store,day,slot):
    """Persist retry claim and running marker together, before any crawl."""
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',day) or not re.fullmatch(r'\d{2}:\d{2}',slot): raise ValueError('invalid slot identity')
    db=store.db;schema(db);db.execute('BEGIN IMMEDIATE')
    try:
        row=db.execute('SELECT count FROM slot_attempts WHERE day=? AND slot=?',(day,slot)).fetchone()
        if row and row[0]>=3:
            db.execute('COMMIT');return False
        db.execute('INSERT INTO slot_attempts VALUES (?,?,1) ON CONFLICT(day,slot) DO UPDATE SET count=count+1',(day,slot))
        db.execute("INSERT INTO slot_runs(day,slot,state) VALUES (?,?,'running') ON CONFLICT(day,slot) DO UPDATE SET state='running'",(day,slot))
        db.execute('COMMIT');return True
    except BaseException:
        db.execute('ROLLBACK');raise


def finish(store,day,slot,outcome,url=None):
    if outcome not in ('published','held','unknown'): raise ValueError('invalid slot outcome')
    if outcome=='published' and (not isinstance(url,str) or not url): raise ValueError('verified publication URL required')
    db=store.db; schema(db);db.execute('BEGIN IMMEDIATE')
    try:
        changed=db.execute("UPDATE slot_runs SET state=? WHERE day=? AND slot=? AND state='running'",(outcome,day,slot)).rowcount
        if changed!=1: raise ValueError('slot run unavailable')
        payload={'kind':'scheduled_slot','day':day,'slot':slot,'state':outcome,'url':url if outcome=='published' else None,
                 'reason':('Cần đối soát kết quả đăng' if outcome=='unknown' else 'Không đạt điều kiện đăng hoặc không có bài phù hợp' if outcome=='held' else None)}
        previous=db.execute('SELECT outcome FROM slot_notice_events WHERE day=? AND slot=? ORDER BY event_id DESC LIMIT 1',(day,slot)).fetchone()
        if not previous or previous['outcome']!=outcome:
            # Supersede unsent stale results; in-flight results remain uncertain.
            db.execute('UPDATE slot_notice_events SET delivered=2 WHERE day=? AND slot=? AND delivered=0',(day,slot))
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='telegram_outbox'").fetchone():
                key=-int(day.replace('-','')+slot.replace(':',''))
                db.execute("UPDATE telegram_outbox SET state='superseded' WHERE message_id=? AND kind LIKE 'slot_%' AND state='pending'",(key,))
            db.execute('INSERT INTO slot_notice_events(day,slot,outcome,payload) VALUES (?,?,?,?)',(day,slot,outcome,json.dumps(payload,ensure_ascii=False,sort_keys=True)))
        db.execute('COMMIT')
    except BaseException:
        db.execute('ROLLBACK');raise


def recover(store):
    """Call only while holding worker_lock, before scheduled work restarts."""
    schema(store.db)
    for row in store.db.execute("SELECT day,slot FROM slot_runs WHERE state='running'").fetchall():
        finish(store,row['day'],row['slot'],'unknown')
