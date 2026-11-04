"""Read-only identification of an expired, safely replaceable candidate."""
from datetime import datetime
import hashlib
from pathlib import Path
from .localization import strict_json
from .official_store import _check_candidate_hashes,_schema
from .store import identity

def expired_candidate(store,item,now):
    if now.tzinfo is None:raise ValueError('timezone required')
    if store.db.execute("SELECT 1 FROM attempts WHERE state IN ('intent','unknown') LIMIT 1").fetchone():
        raise ValueError('unresolved attempt requires reconciliation')
    if not store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='official_candidates'").fetchone():return None
    aid=identity(item['url'])
    row=store.db.execute("SELECT a.state,a.source_url,a.manifest_sha,o.evidence_path,o.record_sha,o.payload_sha FROM articles a JOIN official_candidates o ON o.article_id=a.id WHERE a.id=?",(aid,)).fetchone()
    if not row or row['state']!='official_ready':return None
    if row['source_url']!=item['url'] or row['manifest_sha']!=row['record_sha']:raise ValueError('restage identity mismatch')
    if store.db.execute("SELECT 1 FROM attempts WHERE article_id=? AND state IN ('intent','unknown','published') LIMIT 1",(aid,)).fetchone():
        raise ValueError('restage attempt requires reconciliation')
    folder=Path(row['evidence_path']);_check_candidate_hashes(store,folder,row['record_sha'])
    if folder.parent.parent.name!=item['id']:raise ValueError('restage source path mismatch')
    payload=(folder/'payload.json').read_bytes()
    if hashlib.sha256(payload).hexdigest()!=row['payload_sha']:raise ValueError('restage payload changed')
    value=strict_json(payload.decode('utf-8'))
    if value.get('source',{}).get('id')!=item['id'] or value['source'].get('url')!=item['url']:
        raise ValueError('restage source changed')
    try:when=datetime.fromisoformat(value['published_at'].replace('Z','+00:00'))
    except (KeyError,AttributeError,ValueError):raise ValueError('restage timestamp invalid') from None
    if when.tzinfo is None:raise ValueError('restage timestamp missing timezone')
    if when>now:return None
    return {'source_sha':folder.parent.name,'record_sha':row['record_sha']}

def verify_new_candidate(store,item,previous,new_record_sha,target,now):
    if store.db.execute("SELECT 1 FROM attempts WHERE state IN ('intent','unknown') LIMIT 1").fetchone():
        raise ValueError('restage outcome requires reconciliation')
    row=store.db.execute("SELECT a.state,a.source_url,a.manifest_sha,o.evidence_path,o.record_sha,o.payload_sha FROM articles a JOIN official_candidates o ON o.article_id=a.id WHERE a.id=?",(identity(item['url']),)).fetchone()
    if (not row or row['state']!='official_preparing' or row['source_url']!=item['url'] or row['record_sha']!=new_record_sha
            or row['manifest_sha']!=new_record_sha or new_record_sha==previous['record_sha']):
        raise ValueError('new restage revision not committed')
    if store.db.execute("SELECT 1 FROM attempts WHERE article_id=? AND state IN ('intent','unknown','published') LIMIT 1",(identity(item['url']),)).fetchone():
        raise ValueError('restage outcome requires reconciliation')
    folder=Path(row['evidence_path']);_check_candidate_hashes(store,folder,new_record_sha)
    if folder.parent.parent.name!=item['id'] or folder.parent.name!=previous['source_sha']:
        raise ValueError('restage source changed')
    payload=(folder/'payload.json').read_bytes()
    if hashlib.sha256(payload).hexdigest()!=row['payload_sha']:raise ValueError('new restage payload changed')
    value=strict_json(payload.decode('utf-8'))
    if value.get('source',{}).get('id')!=item['id'] or value['source'].get('url')!=item['url']:
        raise ValueError('new restage source changed')
    try:when=datetime.fromisoformat(value['published_at'].replace('Z','+00:00'))
    except (KeyError,AttributeError,ValueError):raise ValueError('new restage time invalid') from None
    if when.tzinfo is None or when!=target or when<=now:raise ValueError('new restage time changed')

def begin_restage(store,item,previous,now):
    """Make the old candidate unreservable before any new revision exists."""
    _schema(store);store.db.execute('BEGIN IMMEDIATE')
    try:
        if expired_candidate(store,item,now)!=previous:raise ValueError('restage evidence changed')
        aid=identity(item['url'])
        existing=store.db.execute('SELECT state FROM official_restage_intents WHERE article_id=?',(aid,)).fetchone()
        if existing and existing['state']!='finalized':raise ValueError('restage already pending')
        store.db.execute("INSERT INTO official_restage_intents VALUES (?,?,'preparing') ON CONFLICT(article_id) DO UPDATE SET old_record_sha=excluded.old_record_sha,state='preparing'",(aid,previous['record_sha']))
        changed=store.db.execute("UPDATE articles SET state='official_preparing' WHERE id=? AND state='official_ready'",(aid,))
        if changed.rowcount!=1:raise ValueError('restage state changed')
        store.db.execute('COMMIT')
    except BaseException:
        store.db.execute('ROLLBACK');raise

def finalize_restage(store,item,previous,new_record_sha,target,now):
    """Validate and release in the same Store write transaction."""
    _schema(store);store.db.execute('BEGIN IMMEDIATE')
    try:
        aid=identity(item['url'])
        intent=store.db.execute('SELECT old_record_sha,state FROM official_restage_intents WHERE article_id=?',(aid,)).fetchone()
        if not intent or intent['state']!='preparing' or intent['old_record_sha']!=previous['record_sha']:
            raise ValueError('restage intent unavailable')
        verify_new_candidate(store,item,previous,new_record_sha,target,now)
        changed=store.db.execute("UPDATE articles SET state='official_ready' WHERE id=? AND state='official_preparing'",(aid,))
        if changed.rowcount!=1:raise ValueError('restage state changed')
        store.db.execute("UPDATE official_restage_intents SET state='finalized' WHERE article_id=?",(aid,))
        store.db.execute('COMMIT')
    except BaseException:
        store.db.execute('ROLLBACK');raise
