"""Transactional command journal for Source Mirror; not a publish path."""
import hashlib
import json
from .telegram_command_contract import parse
from .store import local_day

SUPPORTED = frozenset({'scratchmagazine.co.uk', 'www.scratchmagazine.co.uk'})


def ensure_outbox(db):
    db.execute('''CREATE TABLE IF NOT EXISTS telegram_outbox (
        notice_id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
        kind TEXT NOT NULL, state TEXT NOT NULL, payload TEXT NOT NULL,
        UNIQUE(owner_id,message_id,kind))''')


def execute(store, owner_id, message_id, text):
    if type(owner_id) is not int or owner_id <= 0 or type(message_id) is not int or message_id <= 0:
        raise ValueError('invalid message identity')
    command = parse(text, SUPPORTED)
    db = store.db
    db.execute('''CREATE TABLE IF NOT EXISTS telegram_commands (
        owner_id INTEGER NOT NULL, message_id INTEGER NOT NULL, command_sha256 TEXT NOT NULL,
        result TEXT NOT NULL, PRIMARY KEY(owner_id, message_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS telegram_crawl_queue (
        owner_id INTEGER NOT NULL, message_id INTEGER NOT NULL, url TEXT NOT NULL,
        state TEXT NOT NULL, result_url TEXT, article_id TEXT, PRIMARY KEY(owner_id, message_id))''')
    ensure_outbox(db)
    digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
    db.execute('BEGIN IMMEDIATE')
    try:
        prior = db.execute('SELECT command_sha256,result FROM telegram_commands WHERE owner_id=? AND message_id=?', (owner_id, message_id)).fetchone()
        if prior:
            if prior['command_sha256'] != digest: raise ValueError('message identity collision')
            result = json.loads(prior['result'])
        elif db.execute('SELECT 1 FROM telegram_commands WHERE owner_id=? AND message_id>? LIMIT 1', (owner_id, message_id)).fetchone():
            raise ValueError('out-of-order message')
        elif command['operation'] == 'status':
            queued = db.execute("SELECT state, count(*) AS n FROM telegram_crawl_queue GROUP BY state").fetchall()
            latest = db.execute('SELECT message_id,state,result_url FROM telegram_crawl_queue WHERE owner_id=? ORDER BY message_id DESC LIMIT 1', (owner_id,)).fetchone()
            notices=db.execute('SELECT state,count(*) AS n FROM telegram_outbox WHERE owner_id=? GROUP BY state',(owner_id,)).fetchall()
            articles={x['state']:x['n'] for x in db.execute("SELECT state,count(*) AS n FROM articles WHERE state IN ('ready','held') GROUP BY state").fetchall()}
            published=db.execute("SELECT count(*) FROM attempts WHERE state='published' AND published_day=?",(local_day(),)).fetchone()[0]
            result = {'state': 'paused' if store.paused() else 'running', 'queue': {x['state']: x['n'] for x in queued}, 'articles':{'ready':articles.get('ready',0),'held':articles.get('held',0)}, 'published_today':published, 'latest':dict(latest) if latest else None, 'notifications':{x['state']:x['n'] for x in notices}, 'publication_enabled': False}
        elif command['operation'] in ('pause', 'resume'):
            store.set_paused(command['operation'] == 'pause')
            result = {'state': 'paused' if store.paused() else 'running', 'inflight_may_continue': True, 'publication_enabled': False}
        else:
            from urllib.parse import urlsplit
            if not urlsplit(command['url']).path.startswith('/nail_art/'):
                raise ValueError('unsupported article path')
            db.execute('INSERT INTO telegram_crawl_queue (owner_id,message_id,url,state) VALUES (?,?,?,?)', (owner_id, message_id, command['url'], 'queued'))
            result = {'state': 'queued', 'publication_enabled': False}
        if not prior:
            db.execute('INSERT INTO telegram_commands VALUES (?,?,?,?)', (owner_id, message_id, digest, json.dumps(result, sort_keys=True)))
        db.execute('COMMIT')
        return {**result, 'replayed': bool(prior)}
    except BaseException:
        db.execute('ROLLBACK')
        raise


def notification(store, owner, operation, message_id=None):
    db=store.db
    ensure_outbox(db)
    db.execute('BEGIN IMMEDIATE')
    try:
        if operation=='claim':
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='slot_notice_events'").fetchone():
                slot=db.execute("SELECT event_id,day,slot,outcome,payload FROM slot_notice_events WHERE delivered=0 ORDER BY event_id LIMIT 1").fetchone()
                if slot:
                    key=-int(slot['day'].replace('-','')+slot['slot'].replace(':',''))
                    db.execute("INSERT OR IGNORE INTO telegram_outbox(owner_id,message_id,kind,state,payload) VALUES (?,?,?,?,?)",(owner,key,'slot_'+str(slot['event_id']),'pending',slot['payload']))
                    db.execute('UPDATE slot_notice_events SET delivered=1 WHERE event_id=?',(slot['event_id'],))
            row=db.execute("SELECT notice_id,message_id,payload FROM telegram_outbox WHERE owner_id=? AND state='pending' ORDER BY notice_id LIMIT 1",(owner,)).fetchone()
            if row:
                db.execute("UPDATE telegram_outbox SET state='sending' WHERE owner_id=? AND notice_id=? AND state='pending'",(owner,row['notice_id']))
                result={'notice_id':row['notice_id'],'message_id':row['message_id'],'payload':json.loads(row['payload'])}
            else: result=None
        elif operation=='recover' and message_id is None:
            count=db.execute("UPDATE telegram_outbox SET state='unknown' WHERE owner_id=? AND state='sending'",(owner,)).rowcount
            result={'recovered_unknown':count}
        elif operation in ('sent','unknown') and type(message_id) is int and message_id>0:
            changed=db.execute("UPDATE telegram_outbox SET state=? WHERE owner_id=? AND notice_id=? AND state='sending'",(operation,owner,message_id)).rowcount
            if changed!=1: raise ValueError('notification state mismatch')
            result={'state':operation}
        else: raise ValueError('invalid notification operation')
        db.execute('COMMIT'); return result
    except BaseException:
        db.execute('ROLLBACK'); raise
