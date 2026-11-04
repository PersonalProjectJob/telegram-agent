"""Crawl at most one queued URL under the caller's existing worker lock."""
def process_one(config, store, crawler=None, publisher=None):
    exists=store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='telegram_crawl_queue'").fetchone()
    if not exists: return None
    store.db.execute('BEGIN IMMEDIATE')
    url=None
    try:
        # Caller holds worker_lock, so an older processing row survived a crash.
        # Never automatically retry an ambiguous publication outcome.
        import json
        orphans=store.db.execute("SELECT owner_id,message_id FROM telegram_crawl_queue WHERE state='processing'").fetchall()
        for orphan in orphans:
            store.db.execute("UPDATE telegram_crawl_queue SET state='unknown' WHERE owner_id=? AND message_id=? AND state='processing'",(orphan['owner_id'],orphan['message_id']))
            store.db.execute("INSERT OR IGNORE INTO telegram_outbox(owner_id,message_id,kind,state,payload) VALUES (?,?,?,?,?)",(orphan['owner_id'],orphan['message_id'],'outcome','pending',json.dumps({'state':'unknown','result_url':None})))
        if store.db.execute("SELECT 1 FROM telegram_crawl_queue WHERE state='unknown' LIMIT 1").fetchone():
            store.db.execute('COMMIT'); return None
        if store.paused():
            store.db.execute('COMMIT'); return None
        row=store.db.execute("SELECT owner_id,message_id,url FROM telegram_crawl_queue WHERE state='queued' ORDER BY message_id LIMIT 1").fetchone()
        if row is None:
            store.db.execute('COMMIT'); return None
        store.db.execute("UPDATE telegram_crawl_queue SET state='processing' WHERE owner_id=? AND message_id=? AND state='queued'",(row['owner_id'],row['message_id']))
        store.db.execute('COMMIT')
    except BaseException:
        store.db.execute('ROLLBACK'); raise
    if crawler is None:
        from .crawler import crawl_url
        crawler=crawl_url
    try:
        result=crawler(config,store,row['url'])
        if not isinstance(result,dict) or result.get('state') not in ('captured','existing','held'): raise ValueError('invalid crawl result')
        if result['state']=='existing': state='existing'; url=result.get('url')
        elif result['state']=='held': state='held'
        else:
            article_id=result.get('article_id')
            if not isinstance(article_id,str) or len(article_id)!=64: raise ValueError('missing captured identity')
            store.db.execute("UPDATE telegram_crawl_queue SET article_id=? WHERE owner_id=? AND message_id=? AND state='processing'",(article_id,row['owner_id'],row['message_id']))
            if publisher is None:
                from .__main__ import once
                publisher=lambda cfg, st, aid: once(cfg,st,requested_article_id=aid)
            url=publisher(config,store,article_id)
            if not isinstance(url,str) or not url: raise ValueError('publication outcome unavailable')
            state='published'
    except Exception:
        unknown=store.db.execute("SELECT 1 FROM attempts WHERE state IN ('intent','unknown') LIMIT 1").fetchone()
        state='unknown' if unknown else 'held'
    store.db.execute('BEGIN IMMEDIATE')
    try:
        store.db.execute("UPDATE telegram_crawl_queue SET state=?,result_url=? WHERE owner_id=? AND message_id=? AND state='processing'",(state,url if state in ('published','existing') else None,row['owner_id'],row['message_id']))
        payload={'state':state,'result_url':url if state in ('published','existing') else None}
        store.db.execute("INSERT OR IGNORE INTO telegram_outbox(owner_id,message_id,kind,state,payload) VALUES (?,?,?,?,?)",(row['owner_id'],row['message_id'],'outcome','pending',json.dumps(payload,sort_keys=True)))
        store.db.execute('COMMIT')
    except BaseException:
        store.db.execute('ROLLBACK'); raise
    return state


def reconcile_unknown(store, owner_id, message_id):
    """Caller must hold worker_lock; trust only existing reservation evidence."""
    store.db.execute('BEGIN IMMEDIATE')
    try:
        row=store.db.execute("SELECT article_id FROM telegram_crawl_queue WHERE owner_id=? AND message_id=? AND state='unknown'",(owner_id,message_id)).fetchone()
        if not row: raise ValueError('unknown queue item not found')
        aid=row['article_id']
        attempt=store.db.execute('SELECT state,target_url FROM attempts WHERE article_id=? ORDER BY id DESC LIMIT 1',(aid,)).fetchone() if aid else None
        if attempt and attempt['state']=='published' and attempt['target_url']:
            state,url='published',attempt['target_url']
        elif attempt and attempt['state'] in ('intent','unknown'):
            raise ValueError('publication attempt requires reconciliation first')
        else: state,url='held',None
        store.db.execute("UPDATE telegram_crawl_queue SET state=?,result_url=? WHERE owner_id=? AND message_id=? AND state='unknown'",(state,url,owner_id,message_id))
        import json
        store.db.execute("UPDATE telegram_outbox SET state='superseded' WHERE owner_id=? AND message_id=? AND state='pending'",(owner_id,message_id))
        payload=json.dumps({'state':state,'result_url':url,'resolved':True},sort_keys=True)
        store.db.execute("INSERT OR IGNORE INTO telegram_outbox(owner_id,message_id,kind,state,payload) VALUES (?,?,?,?,?)",(owner_id,message_id,'resolution','pending',payload))
        store.db.execute('COMMIT');return {'state':state,'result_url':url}
    except BaseException:
        store.db.execute('ROLLBACK');raise
