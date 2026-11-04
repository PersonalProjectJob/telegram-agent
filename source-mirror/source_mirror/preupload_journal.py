"""Offline candidate journal for pre-reservation body uploads; no browser calls on import."""
import hashlib,json,re,sqlite3,uuid

class JournalHold(ValueError):pass

def setup(db):
    db.executescript("""
      CREATE TABLE IF NOT EXISTS body_preupload_sessions(
        session_id TEXT PRIMARY KEY, article_id TEXT NOT NULL, manifest_sha TEXT NOT NULL,
        payload_sha TEXT NOT NULL, plan_sha TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('open','closed')));
      CREATE TABLE IF NOT EXISTS body_preuploads(
        session_id TEXT NOT NULL REFERENCES body_preupload_sessions(session_id),
        ordinal INTEGER NOT NULL, source_sha TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('started','bound','omitted')),
        receipt_json TEXT, reason TEXT, PRIMARY KEY(session_id,ordinal));
      CREATE TABLE IF NOT EXISTS body_preupload_decisions(
        session_id TEXT NOT NULL, ordinal INTEGER NOT NULL, decision TEXT NOT NULL, reason TEXT NOT NULL,
        PRIMARY KEY(session_id,ordinal,decision));
    """)

def _hash(x):return isinstance(x,str) and re.fullmatch('[a-f0-9]{64}',x) is not None

def begin(db,article_id,manifest_sha,payload_sha,plan_sha):
    if any(not _hash(x) for x in (article_id,manifest_sha,payload_sha,plan_sha)):raise JournalHold('invalid binding')
    session=uuid.uuid4().hex
    db.execute('INSERT INTO body_preupload_sessions VALUES(?,?,?,?,?,?)',(session,article_id,manifest_sha,payload_sha,plan_sha,'open'))
    return session

def started(db,session,ordinal,source_sha):
    if type(ordinal) is not int or not 1<=ordinal<=5 or not _hash(source_sha):raise JournalHold('invalid upload')
    if not db.execute("SELECT 1 FROM body_preupload_sessions WHERE session_id=? AND state='open'",(session,)).fetchone():raise JournalHold('context unavailable')
    db.execute("INSERT INTO body_preuploads VALUES(?,?,?,'started',NULL,NULL)",(session,ordinal,source_sha))

def bound(db,session,ordinal,request_bytes,content_type,source_bytes,response):
    from .scratch_native_guard import bind_upload
    try:verified=bind_upload(request_bytes,content_type,source_bytes,response)
    except Exception as exc:raise JournalHold('native multipart or response mismatch') from exc
    receipt={k:verified[k] for k in ('source_sha256','request_sha256','response')}
    raw=json.dumps(receipt,sort_keys=True,separators=(',',':'))
    if len(raw)>12000:raise JournalHold('receipt too large')
    cursor=db.execute("UPDATE body_preuploads SET status='bound',receipt_json=? WHERE session_id=? AND ordinal=? AND status='started' AND source_sha=? AND EXISTS (SELECT 1 FROM body_preupload_sessions WHERE session_id=? AND state='open')",(raw,session,ordinal,receipt['source_sha256'],session))
    if cursor.rowcount!=1:raise JournalHold('upload binding changed')

def close_context(db,session,context,reason='context_closed_before_upload_receipt'):
    """Only record omissions after the actual native context.close returned."""
    if not isinstance(reason,str) or not reason or len(reason)>200:raise JournalHold('invalid reason')
    context.close()
    db.execute('BEGIN IMMEDIATE')
    try:
        cursor=db.execute("UPDATE body_preupload_sessions SET state='closed' WHERE session_id=? AND state='open'",(session,))
        if cursor.rowcount!=1:raise JournalHold('context unavailable')
        db.execute("UPDATE body_preuploads SET status='omitted',reason=? WHERE session_id=? AND status='started'",(reason,session))
        db.execute('COMMIT')
    except BaseException:db.execute('ROLLBACK');raise

def completed(db,session,article_id,manifest_sha,payload_sha,plan_sha,live_evidence=None):
    """Revalidate in-memory native request proof; never reuse a stored receipt alone."""
    from .scratch_native_guard import bind_upload
    row=db.execute("SELECT article_id,manifest_sha,payload_sha,plan_sha,state FROM body_preupload_sessions WHERE session_id=?",(session,)).fetchone()
    if row is None or tuple(row)!=(article_id,manifest_sha,payload_sha,plan_sha,'closed'):raise JournalHold('session or binding unavailable')
    rows=db.execute('SELECT ordinal,status,source_sha,receipt_json,reason FROM body_preuploads WHERE session_id=? ORDER BY ordinal',(session,)).fetchall()
    if any(r[1] not in ('bound','omitted') for r in rows):raise JournalHold('unresolved upload')
    live_evidence=live_evidence if isinstance(live_evidence,dict) else {}
    result=[]
    for n,st,sha,raw,reason in rows:
        receipt=json.loads(raw) if raw else None
        prior=db.execute("SELECT reason FROM body_preupload_decisions WHERE session_id=? AND ordinal=? AND decision='omit'",(session,n)).fetchone()
        if prior:
            st='omitted';reason=prior[0];receipt=None
        if st=='bound':
            proof=live_evidence.get(n)
            try:
                if not isinstance(proof,dict) or set(proof)!={'request_bytes','content_type','source_bytes','response'}:raise ValueError('missing live native proof')
                verified=bind_upload(proof['request_bytes'],proof['content_type'],proof['source_bytes'],proof['response'])
                current={k:verified[k] for k in ('source_sha256','request_sha256','response')}
                if current!=receipt or current['source_sha256']!=sha:raise ValueError('native proof changed')
            except Exception:
                st='omitted';reason='missing_or_invalid_native_proof';receipt=None
                db.execute('INSERT OR IGNORE INTO body_preupload_decisions VALUES(?,?,?,?)',(session,n,'omit',reason))
        result.append({'ordinal':n,'status':st,'source_sha256':sha,'receipt':receipt,'reason':reason})
    return result
