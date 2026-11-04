"""Durable state and immutable snapshot validation for Source Mirror."""
import hashlib
import json
import os
import sqlite3
import tempfile
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from zoneinfo import ZoneInfo

ZONE = ZoneInfo('Asia/Ho_Chi_Minh')

def canonical(url):
    u = urlsplit(url)
    if u.scheme not in ('https', 'http') or not u.hostname or u.username or u.password:
        raise ValueError('invalid source URL')
    host = u.hostname.lower()
    port = u.port
    netloc = host + (f':{port}' if port and (u.scheme, port) not in (('http',80),('https',443)) else '')
    query = urlencode([(k,v) for k,v in parse_qsl(u.query, keep_blank_values=True) if not k.lower().startswith('utm_') and k.lower() not in ('fbclid','gclid')])
    return urlunsplit((u.scheme.lower(), netloc, u.path or '/', query, ''))

def identity(url):
    return hashlib.sha256(canonical(url).encode()).hexdigest()

def snapshot_identity(meta):
    if not isinstance(meta,dict):raise ValueError('invalid snapshot metadata')
    if meta.get('content_origin')!='ai_original':return identity(meta['source_url'])
    import re,unicodedata
    url=meta.get('source_url');title=meta.get('title')
    if not isinstance(url,str) or canonical(url)!=url or not urlsplit(url).hostname.endswith('.gov'):raise ValueError('invalid original reference')
    if not isinstance(title,str) or not 5<=len(title)<=200:raise ValueError('invalid original title')
    key=re.sub(r'[^\w]+',' ',unicodedata.normalize('NFKC',title).casefold()).strip()
    if not key:raise ValueError('invalid original identity')
    return hashlib.sha256(('nailhub-ai-original-v1\0'+url+'\0'+key).encode('utf-8')).hexdigest()

def sha(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for part in iter(lambda:f.read(1024*1024),b''): h.update(part)
    return h.hexdigest()

def local_day(now=None):
    return (now or datetime.now(timezone.utc)).astimezone(ZONE).date().isoformat()

def verify_snapshot(root, article_id):
    if len(article_id)!=64 or any(c not in '0123456789abcdef' for c in article_id): raise ValueError('invalid identity')
    folder=Path(root)/'articles'/article_id
    if folder.is_symlink() or not folder.is_dir(): raise ValueError('missing snapshot')
    manifest=json.loads((folder/'snapshot-manifest.json').read_text(encoding='utf-8'))
    files=manifest['files']
    if not isinstance(files,dict) or not {'metadata.json','content.json','content.html','media-manifest.json'} <= files.keys(): raise ValueError('incomplete manifest')
    for rel,digest in files.items():
        p=Path(rel)
        if p.is_absolute() or '..' in p.parts or not p.parts or p.parts[0] == 'snapshot-manifest.json': raise ValueError('unsafe snapshot path')
        path=folder/p
        if any(x.is_symlink() for x in [path,*list(path.parents)[:len(p.parts)-1]]) or not path.is_file() or sha(path)!=digest: raise ValueError('snapshot missing or corrupt')
    meta=json.loads((folder/'metadata.json').read_text(encoding='utf-8'))
    if snapshot_identity(meta) != article_id: raise ValueError('identity mismatch')
    media=json.loads((folder/'media-manifest.json').read_text(encoding='utf-8'))
    for item in media:
        if item['path'] not in files: raise ValueError('unmanifested media')
    return folder

class Store:
    def __init__(self, root):
        self.root=Path(root).resolve(); self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.db=sqlite3.connect(self.root/'state.sqlite3', timeout=30, isolation_level=None)
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL'); self.db.execute('PRAGMA synchronous=FULL'); self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY,value TEXT NOT NULL);
        INSERT OR IGNORE INTO settings VALUES ('paused','1');
        CREATE TABLE IF NOT EXISTS articles (id TEXT PRIMARY KEY, source_url TEXT UNIQUE NOT NULL, title TEXT NOT NULL, source_date TEXT, state TEXT NOT NULL, reason TEXT, created_at TEXT NOT NULL, manifest_sha TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS attempts (id INTEGER PRIMARY KEY, article_id TEXT NOT NULL REFERENCES articles(id), reserved_day TEXT NOT NULL, state TEXT NOT NULL, target_url TEXT, published_day TEXT, created_at TEXT NOT NULL, note TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS one_active_article ON attempts(article_id) WHERE state IN ('intent','unknown','published');
        CREATE TABLE IF NOT EXISTS slots (day TEXT NOT NULL, slot TEXT NOT NULL, PRIMARY KEY(day,slot));
        CREATE TABLE IF NOT EXISTS slot_attempts (day TEXT NOT NULL, slot TEXT NOT NULL, count INTEGER NOT NULL, PRIMARY KEY(day,slot));
        CREATE TABLE IF NOT EXISTS attempt_phases (attempt_id INTEGER PRIMARY KEY REFERENCES attempts(id), phase TEXT NOT NULL CHECK(phase IN ('pre_create','upload_closed','create_may_have_been_sent','upload_failed')), slot TEXT);
        CREATE TABLE IF NOT EXISTS slot_retry_windows (day TEXT NOT NULL,slot TEXT NOT NULL, PRIMARY KEY(day,slot));
        CREATE TABLE IF NOT EXISTS upload_journal (id INTEGER PRIMARY KEY, attempt_id INTEGER NOT NULL REFERENCES attempts(id), ordinal INTEGER NOT NULL, source_sha256 TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('started','bound')), receipt_json TEXT, UNIQUE(attempt_id,ordinal));''')
    def close(self): self.db.close()
    def paused(self): return self.db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[0]=='1'
    def set_paused(self, value): self.db.execute("UPDATE settings SET value=? WHERE key='paused'",('1' if value else '0',))
    def add_ready(self, url, title, source_date=None):
        url=canonical(url); aid=identity(url); verify_snapshot(self.root,aid)
        self.db.execute("INSERT INTO articles VALUES (?,?,?,?, 'ready',NULL,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,source_date=excluded.source_date,state='ready',reason=NULL,manifest_sha=excluded.manifest_sha WHERE articles.state='held' AND COALESCE(articles.reason,'') NOT LIKE 'localization:%' AND COALESCE(articles.reason,'') NOT LIKE 'topic:%' AND COALESCE(articles.reason,'') NOT LIKE 'upload:%'",(aid,url,title,source_date,datetime.now(timezone.utc).isoformat(),sha(self.root/'articles'/aid/'snapshot-manifest.json')))
        return aid
    def add_original_ready(self, article_id):
        folder=verify_snapshot(self.root,article_id)
        meta=json.loads((folder/'metadata.json').read_text(encoding='utf-8'))
        if meta.get('content_origin')!='ai_original' or snapshot_identity(meta)!=article_id:raise ValueError('original identity mismatch')
        key='urn:nailhub:ai-original:'+article_id
        self.db.execute("INSERT OR IGNORE INTO articles VALUES (?,?,?,NULL,'ready',NULL,?,?)",(article_id,key,meta['title'],datetime.now(timezone.utc).isoformat(),sha(folder/'snapshot-manifest.json')))
        row=self.db.execute('SELECT source_url,title,state,manifest_sha FROM articles WHERE id=?',(article_id,)).fetchone()
        if not row or row['source_url']!=key or row['title']!=meta['title'] or row['manifest_sha']!=sha(folder/'snapshot-manifest.json'):raise ValueError('original record conflict')
        return article_id
    def hold(self,url,reason):
        url=canonical(url); aid=identity(url)
        self.db.execute("INSERT INTO articles VALUES (?,?,?,NULL,'held',?,?, '') ON CONFLICT(id) DO UPDATE SET reason=excluded.reason WHERE articles.state='held' AND COALESCE(articles.reason,'') NOT LIKE 'localization:%' AND COALESCE(articles.reason,'') NOT LIKE 'topic:%' AND COALESCE(articles.reason,'') NOT LIKE 'upload:%'",(aid,url,'',reason,datetime.now(timezone.utc).isoformat()))
    def quality_hold(self, article_id, reason):
        self.db.execute("UPDATE articles SET state='held',reason=? WHERE id=? AND state='ready'",('localization:'+reason,article_id))
    def topic_hold(self, article_id, reason):
        self.db.execute("UPDATE articles SET state='held',reason=? WHERE id=? AND state='ready'",('topic:'+reason,article_id))
    def ready_candidates(self, limit=8):
        if limit is None:
            return [dict(row) for row in self.db.execute("SELECT * FROM articles WHERE state='ready' ORDER BY COALESCE(source_date,'') DESC, created_at DESC")]
        return [dict(row) for row in self.db.execute("SELECT * FROM articles WHERE state='ready' ORDER BY COALESCE(source_date,'') DESC, created_at DESC LIMIT ?",(limit,))]
    def reserve(self, day=None, slot=None, article_id=None, *, now=None):
        day=day or local_day(); self.db.execute('BEGIN IMMEDIATE')
        try:
            if self.paused(): raise ValueError('paused')
            if self.db.execute("SELECT 1 FROM attempts WHERE state IN ('intent','unknown') LIMIT 1").fetchone(): raise ValueError('unresolved attempt requires reconciliation')
            count=self.db.execute("SELECT count(*) FROM attempts WHERE (state='published' AND published_day=?) OR (state IN ('intent','unknown') AND reserved_day=?)",(day,day)).fetchone()[0]
            if count>=4: raise ValueError('daily quota reached')
            rows=self.db.execute("SELECT * FROM articles WHERE state='ready' AND (? IS NULL OR id=?) ORDER BY COALESCE(source_date,'') DESC, created_at DESC",(article_id,article_id)).fetchall()
            if not rows: raise ValueError('no ready articles')
            article=rows[0]; verify_snapshot(self.root,article['id'])
            if sha(self.root/'articles'/article['id']/'snapshot-manifest.json') != article['manifest_sha']: raise ValueError('snapshot manifest changed')
            if slot is not None:
                occupied=self.db.execute('SELECT 1 FROM slots WHERE day=? AND slot=?',(day,slot)).fetchone()
                if occupied:
                    at=now or datetime.now(ZONE)
                    eligible=at.date().isoformat()==day and at.strftime('%H:%M')==slot
                    if not eligible or not self.db.execute('SELECT 1 FROM slot_retry_windows WHERE day=? AND slot=?',(day,slot)).fetchone():raise ValueError('slot already reserved')
                    self.db.execute('DELETE FROM slot_retry_windows WHERE day=? AND slot=?',(day,slot))
                else:self.db.execute('INSERT INTO slots VALUES (?,?)',(day,slot))
            cur=self.db.execute("INSERT INTO attempts(article_id,reserved_day,state,created_at) VALUES (?,?,'intent',?)",(article['id'],day,datetime.now(timezone.utc).isoformat()))
            self.db.execute("INSERT INTO attempt_phases VALUES (?,'pre_create',?)",(cur.lastrowid,slot))
            self.db.execute("UPDATE articles SET state='publishing' WHERE id=?",(article['id'],)); self.db.execute('COMMIT')
            return cur.lastrowid,dict(article)
        except BaseException:
            self.db.execute('ROLLBACK'); raise
    def begin_upload(self, attempt, ordinal, source_sha256):
        if type(ordinal) is not int or ordinal<0 or len(source_sha256)!=64 or any(c not in '0123456789abcdef' for c in source_sha256): raise ValueError('invalid upload intent')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute("SELECT 1 FROM attempts a JOIN attempt_phases p ON p.attempt_id=a.id WHERE a.id=? AND a.state='intent' AND p.phase='pre_create'",(attempt,)).fetchone()
            if not row:raise ValueError('upload phase unavailable')
            cur=self.db.execute("INSERT INTO upload_journal(attempt_id,ordinal,source_sha256,status) VALUES (?,?,?,'started')",(attempt,ordinal,source_sha256))
            self.db.execute('COMMIT');return cur.lastrowid
        except BaseException:self.db.execute('ROLLBACK');raise
    def bind_upload(self, attempt, upload_id, receipt):
        response=receipt['response']; safe={'source_sha256':receipt['source_sha256'],'request_sha256':receipt['request_sha256'],'response':response}
        raw=json.dumps(safe,sort_keys=True,separators=(',',':'))
        if len(raw)>12000:raise ValueError('receipt too large')
        cur=self.db.execute("UPDATE upload_journal SET status='bound',receipt_json=? WHERE id=? AND attempt_id=? AND status='started' AND source_sha256=? AND EXISTS (SELECT 1 FROM attempt_phases p JOIN attempts a ON a.id=p.attempt_id WHERE a.id=? AND a.state='intent' AND p.phase='pre_create')",(raw,upload_id,attempt,receipt['source_sha256'],attempt))
        if cur.rowcount!=1:raise ValueError('upload intent changed')
    def begin_create(self, attempt):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if not self.db.execute("SELECT 1 FROM upload_journal WHERE attempt_id=? AND ordinal=0 AND status='bound'",(attempt,)).fetchone() or self.db.execute("SELECT 1 FROM upload_journal WHERE attempt_id=? AND status!='bound'",(attempt,)).fetchone():raise ValueError('unbound upload')
            cur=self.db.execute("UPDATE attempt_phases SET phase='create_may_have_been_sent' WHERE attempt_id=? AND phase='pre_create' AND EXISTS (SELECT 1 FROM attempts WHERE id=? AND state='intent')",(attempt,attempt))
            if cur.rowcount!=1:raise ValueError('create phase unavailable')
            self.db.execute('COMMIT')
        except BaseException:self.db.execute('ROLLBACK');raise
    def record_upload_closed(self,attempt):
        cur=self.db.execute("UPDATE attempt_phases SET phase='upload_closed' WHERE attempt_id=? AND phase='pre_create' AND EXISTS (SELECT 1 FROM attempts WHERE id=? AND state='intent')",(attempt,attempt))
        if cur.rowcount!=1:raise ValueError('closed upload proof unavailable')
    def recover_closed_uploads(self, *, scheduled_now=None):
        # A durable upload_closed marker is only written after context.close returned.
        rows=self.db.execute("SELECT a.id,a.reserved_day,p.slot FROM attempts a JOIN attempt_phases p ON p.attempt_id=a.id WHERE a.state='intent' AND p.phase='upload_closed'").fetchall()
        for row in rows:
            try:
                eligible=(scheduled_now is None or row['slot'] is None or (row['reserved_day']==scheduled_now.date().isoformat() and row['slot']==scheduled_now.strftime('%H:%M')))
                self.fail_precreate_upload(row['id'],'recovered closed browser after upload failure',release_slot=eligible)
            except ValueError:pass  # Another connection may have completed recovery.
    def fail_precreate_upload(self,attempt,note,*,release_slot=True):
        # Call only after the browser context closed normally; unproven crashes remain unresolved.
        if not isinstance(note,str) or not note:raise ValueError('failure evidence required')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute("SELECT a.article_id,a.reserved_day,p.slot FROM attempts a JOIN attempt_phases p ON p.attempt_id=a.id WHERE a.id=? AND a.state='intent' AND p.phase='upload_closed'",(attempt,)).fetchone()
            if not row:raise ValueError('closed upload proof unavailable')
            self.db.execute("UPDATE attempt_phases SET phase='upload_failed' WHERE attempt_id=?",(attempt,))
            self.db.execute("UPDATE attempts SET state='precreate_failed',note=? WHERE id=?",(note[:500],attempt))
            self.db.execute("UPDATE articles SET state='held',reason=? WHERE id=?",('upload:'+note[:400],row['article_id']))
            if release_slot and row['slot'] is not None:self.db.execute('INSERT OR IGNORE INTO slot_retry_windows VALUES (?,?)',(row['reserved_day'],row['slot']))
            self.db.execute('COMMIT')
        except BaseException:self.db.execute('ROLLBACK');raise
    def slot_available(self,day,slot,now):
        if not self.db.execute('SELECT 1 FROM slots WHERE day=? AND slot=?',(day,slot)).fetchone():return True
        return (now.date().isoformat()==day and now.strftime('%H:%M')==slot and bool(self.db.execute('SELECT 1 FROM slot_retry_windows WHERE day=? AND slot=?',(day,slot)).fetchone()))
    def unknown(self, attempt, note='verification required'):
        self.db.execute("UPDATE attempts SET state='unknown',note=? WHERE id=? AND state='intent'",(note,attempt))
    def published(self, attempt, url, day, note=None):
        u=urlsplit(url)
        if u.scheme!='https' or u.hostname!='nailhub.ai': raise ValueError('invalid public URL')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute("SELECT article_id FROM attempts WHERE id=? AND state IN ('intent','unknown')",(attempt,)).fetchone()
            if not row: raise ValueError('attempt not pending')
            if self.db.execute("SELECT count(*) FROM attempts WHERE state='published' AND published_day=?",(day,)).fetchone()[0]>=4: raise ValueError('published day quota conflict; manual investigation required')
            self.db.execute("UPDATE attempts SET state='published',target_url=?,published_day=?,note=? WHERE id=?",(url,day,note,attempt)); self.db.execute("UPDATE articles SET state='published' WHERE id=?",(row[0],)); self.db.execute('COMMIT')
        except BaseException: self.db.execute('ROLLBACK'); raise
    def release(self, attempt, note):
        if not note.strip(): raise ValueError('evidence note required')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute("SELECT article_id FROM attempts WHERE id=? AND state IN ('intent','unknown')",(attempt,)).fetchone()
            if not row: raise ValueError('attempt not pending')
            has_official=self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='official_candidates'").fetchone()
            is_official=bool(has_official and self.db.execute("SELECT 1 FROM official_candidates WHERE article_id=?",(row[0],)).fetchone())
            state='official_ready' if is_official else 'ready'
            self.db.execute("UPDATE attempts SET state='released',note=? WHERE id=?",(note,attempt)); self.db.execute('UPDATE articles SET state=? WHERE id=?',(state,row[0])); self.db.execute('COMMIT')
        except BaseException: self.db.execute('ROLLBACK'); raise
    def claim_slot_try(self,day,slot):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute('SELECT count FROM slot_attempts WHERE day=? AND slot=?',(day,slot)).fetchone()
            if row and row[0]>=3:
                self.db.execute('COMMIT'); return False
            self.db.execute('INSERT INTO slot_attempts VALUES (?,?,1) ON CONFLICT(day,slot) DO UPDATE SET count=count+1',(day,slot))
            self.db.execute('COMMIT'); return True
        except BaseException: self.db.execute('ROLLBACK'); raise
    def export_reports(self, day=None):
        """Rebuild derived reports from committed state; never changes the snapshot manifest."""
        day=day or local_day()
        def atomic_json(path,payload):
            path.parent.mkdir(parents=True,exist_ok=True)
            name=None
            try:
                with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=path.parent,prefix='.export-',delete=False) as handle:
                    name=handle.name; json.dump(payload,handle,ensure_ascii=False,indent=2); handle.flush(); os.fsync(handle.fileno())
                os.replace(name,path); name=None
            finally:
                if name and os.path.exists(name): os.unlink(name)
        rows=[dict(row) for row in self.db.execute("SELECT a.id,a.source_url,t.id AS attempt,t.target_url,t.published_day,t.note FROM attempts t JOIN articles a ON a.id=t.article_id WHERE t.state='published'")]
        for row in rows:
            if row['source_url'].startswith('urn:nailhub:ai-original:'):
                folder=verify_snapshot(self.root,row['id'])
                meta=json.loads((folder/'metadata.json').read_text(encoding='utf-8'))
                if meta.get('content_origin')!='ai_original':raise ValueError('original report source invalid')
                row['source_url']=meta['source_url'];row['content_origin']='ai_original'
        for row in rows:
            evidence=self.root/'reports'/f"attempt-{row['attempt']}"
            paths={name:str(path.relative_to(self.root)) for name,path in {'before_create':evidence/'before-create.png','admin':evidence/'admin-details.png','guest':evidence/'guest.png'}.items() if path.is_file()}
            base=self.root/'articles'/row['id']/'publication'
            atomic_json(base/'result.json',{'article_id':row['id'],'source_url':row['source_url'],'target_url':row['target_url'],'published_day':row['published_day'],'attempt_id':row['attempt']})
            atomic_json(base/'readback.json',{'attempt_id':row['attempt'],'recording':'operator reconciliation' if row['note'] else 'automated verification','evidence_paths':paths,'operator_note':row['note']})
        published=[dict(r) for r in rows if r['published_day']==day]
        held=[dict(r) for r in self.db.execute("SELECT id,source_url,reason FROM articles WHERE state='held'")]
        unresolved=[dict(r) for r in self.db.execute("SELECT id,article_id,reserved_day,state FROM attempts WHERE state IN ('intent','unknown')")]
        atomic_json(self.root/'reports'/f'{day}.json',{'day':day,'published':published,'published_count':len(published),'daily_limit':4,'reserved_count_for_day':sum(r['reserved_day']==day for r in unresolved),'publication_blocked':bool(unresolved),'held_current':held,'unresolved_current':unresolved})
    def safe_export_reports(self, day=None):
        try: self.export_reports(day)
        except Exception as exc: print(f'report export failed ({type(exc).__name__}); rebuild with status',file=sys.stderr)
    def status(self):
        return {'paused':self.paused(), 'articles':[dict(x) for x in self.db.execute('SELECT id,title,state,reason FROM articles')], 'attempts':[dict(x) for x in self.db.execute('SELECT * FROM attempts ORDER BY id')]}
