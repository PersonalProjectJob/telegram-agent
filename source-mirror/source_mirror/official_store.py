"""Official candidate staging and quota-sharing reservation (disabled).

No caller supplies an evaluator, policy, clock, or authorization verdict.
The official publisher is not integrated, so reservation is hard disabled.
"""
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from .localization import digest,strict_json
from .official_draft import LABELS
from .official_generated_media import is_generated,load_policy as load_generated_policy
from .official_native_guard import _live_inputs
from .official_auto_eligibility import load_media_policy
from .official_sources import SOURCES
from .store import identity,local_day
from .official_publish_policy import load as load_publish_policy
from .official_deadline import SLOTS

OFFICIAL_RESERVATION_ENABLED=True


def _check_evidence(store,folder,record_sha,payload,policy):
    root=store.root;folder=Path(folder)
    if not folder.is_absolute() or folder.is_symlink() or folder!=folder.resolve(): raise ValueError('unsafe official evidence path')
    try: relative=folder.relative_to(root/'official-auto-evidence')
    except ValueError: raise ValueError('official evidence outside state root') from None
    if len(relative.parts)!=3 or relative.parts[-1]!=record_sha: raise ValueError('invalid official evidence identity')
    for i in range(4):
        if (root/'official-auto-evidence'/Path(*relative.parts[:i])).is_symlink(): raise ValueError('unsafe official evidence directory')
    record_path=folder/'record.json'
    if record_path.is_symlink() or record_path.stat().st_size>2_000_000: raise ValueError('invalid official record')
    raw=record_path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=record_sha: raise ValueError('official record changed')
    record=strict_json(raw.decode('utf-8')); manifest=record.get('files')
    if not isinstance(manifest,dict) or not manifest: raise ValueError('official evidence manifest missing')
    for name,expected in manifest.items():
        relative_name=Path(name)
        if relative_name.is_absolute() or '..' in relative_name.parts or not relative_name.parts: raise ValueError('unsafe official evidence file')
        path=folder/relative_name
        if any(p.is_symlink() for p in (path,*list(path.parents)[:len(relative_name.parts)-1])) or not path.is_file() or path.stat().st_size>20_000_000:
            raise ValueError('official evidence file unavailable')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=expected: raise ValueError('official evidence file changed')
    if (record.get('payload_sha256')!=hashlib.sha256(payload).hexdigest() or digest(policy)!=record.get('media_policy_sha256')
            or digest(load_media_policy())!=record.get('media_policy_sha256')): raise ValueError('official payload or policy changed')
    for media in record['media_manifest']:
        if is_generated(media):
            generated_policy=load_generated_policy()
            if generated_policy!=record.get('generated_policy') or digest(generated_policy)!=record.get('generated_policy_sha256') or relative.parts[0] not in generated_policy['source_ids']:raise ValueError('generated policy changed')
            continue  # Fresh boundary already validates immutable evidence.
        matches=[entry for entry in policy['entries'] if isinstance(entry,dict) and entry.get('source_id')==relative.parts[0]
                 and entry.get('image_sha256')==media['sha256']]
        if len(matches)!=1: raise ValueError('official media policy changed')
        try: expiry=datetime.fromisoformat(matches[0]['valid_until_utc'].replace('Z','+00:00'))
        except (KeyError,ValueError,AttributeError): raise ValueError('official media expiry invalid') from None
        if expiry.tzinfo is None or expiry<=datetime.now(timezone.utc): raise ValueError('official media policy expired')
    return record


def _validated(store,folder,record_sha):
    payload,boundary,mapping,originals,policy=_live_inputs(store.root,folder,record_sha)
    if boundary.get('status')!='REVALIDATED_NOT_PUBLISHABLE' or boundary.get('record_sha256')!=record_sha:
        raise ValueError('trusted official boundary unavailable')
    record=_check_evidence(store,folder,record_sha,payload,policy)
    value=strict_json(payload.decode('utf-8')); source=value['source']
    item=next((item for item in SOURCES if item['id']==source['id']),None)
    if (item is None or source['url']!=item['url'] or source['scope']!=item['scope']
            or source['label']!=LABELS[item['id']] or record.get('source_sha256')!=Path(folder).parent.name):
        raise ValueError('official source identity changed')
    return payload,value,policy


def _schema(store):
    store.db.execute('''CREATE TABLE IF NOT EXISTS official_candidates (
        article_id TEXT PRIMARY KEY REFERENCES articles(id), evidence_path TEXT NOT NULL,
        record_sha TEXT NOT NULL, payload_sha TEXT NOT NULL, policy_sha TEXT NOT NULL)''')
    store.db.execute('''CREATE TABLE IF NOT EXISTS official_candidate_versions (
        article_id TEXT NOT NULL REFERENCES articles(id), record_sha TEXT NOT NULL,
        evidence_path TEXT NOT NULL, payload_sha TEXT NOT NULL, policy_sha TEXT NOT NULL,
        created_at TEXT NOT NULL, PRIMARY KEY(article_id,record_sha))''')
    store.db.execute('''CREATE TABLE IF NOT EXISTS official_attempt_slots (
        attempt_id INTEGER PRIMARY KEY REFERENCES attempts(id), day TEXT NOT NULL, slot TEXT NOT NULL)''')
    store.db.execute('''CREATE TABLE IF NOT EXISTS official_restage_intents (
        article_id TEXT PRIMARY KEY REFERENCES articles(id), old_record_sha TEXT NOT NULL,
        state TEXT NOT NULL)''')


def stage(store,evidence_path,record_sha,*,restage=False):
    """Register immutable verified evidence without consuming quota or publishing."""
    payload,value,policy=_validated(store,evidence_path,record_sha)
    url=value['source']['url']; aid=identity(url);title=value['locales']['vi']['title']
    _schema(store);store.db.execute('BEGIN IMMEDIATE')
    try:
        _check_evidence(store,evidence_path,record_sha,payload,policy)
        row=store.db.execute('SELECT * FROM articles WHERE id=?',(aid,)).fetchone()
        if row:
            old=store.db.execute('SELECT * FROM official_candidates WHERE article_id=?',(aid,)).fetchone()
            if not old or row['source_url']!=url:
                raise ValueError('official source already registered')
            if restage:
                intent=store.db.execute("SELECT old_record_sha,state FROM official_restage_intents WHERE article_id=?",(aid,)).fetchone()
                if row['state']!='official_preparing' or not intent or intent['state']!='preparing' or intent['old_record_sha']!=old['record_sha']:
                    raise ValueError('restage intent unavailable')
                if old['record_sha']==record_sha:raise ValueError('restage requires a new revision')
            elif row['state']=='official_preparing':raise ValueError('official restage in progress')
            store.db.execute('INSERT OR IGNORE INTO official_candidate_versions VALUES (?,?,?,?,?,?)',
                             (aid,old['record_sha'],old['evidence_path'],old['payload_sha'],old['policy_sha'],row['created_at']))
            if old['record_sha']==record_sha:
                if old['payload_sha']!=hashlib.sha256(payload).hexdigest() or old['policy_sha']!=digest(policy):
                    raise ValueError('official candidate revision changed')
            else:
                if row['state']!=('official_preparing' if restage else 'official_ready') or store.db.execute("SELECT 1 FROM attempts WHERE article_id=? AND state IN ('intent','unknown','published')",(aid,)).fetchone():
                    raise ValueError('official source revision is locked')
                store.db.execute('UPDATE official_candidates SET evidence_path=?,record_sha=?,payload_sha=?,policy_sha=? WHERE article_id=?',
                                 (str(evidence_path),record_sha,hashlib.sha256(payload).hexdigest(),digest(policy),aid))
                store.db.execute('UPDATE articles SET title=?,manifest_sha=? WHERE id=?',(title,record_sha,aid))
        else:
            if restage:raise ValueError('restage candidate missing')
            store.db.execute("INSERT INTO articles VALUES (?,?,?,NULL,'official_ready',NULL,?,?)",(aid,url,title,datetime.now(timezone.utc).isoformat(),record_sha))
            store.db.execute('INSERT INTO official_candidates VALUES (?,?,?,?,?)',(aid,str(evidence_path),record_sha,hashlib.sha256(payload).hexdigest(),digest(policy)))
        old_version=store.db.execute('SELECT * FROM official_candidate_versions WHERE article_id=? AND record_sha=?',(aid,record_sha)).fetchone()
        if old_version:
            if (old_version['evidence_path']!=str(evidence_path) or old_version['payload_sha']!=hashlib.sha256(payload).hexdigest()
                    or old_version['policy_sha']!=digest(policy)): raise ValueError('official revision history changed')
        else:
            store.db.execute('INSERT INTO official_candidate_versions VALUES (?,?,?,?,?,?)',
                             (aid,record_sha,str(evidence_path),hashlib.sha256(payload).hexdigest(),digest(policy),datetime.now(timezone.utc).isoformat()))
        store.db.execute('COMMIT');return aid
    except BaseException:
        store.db.execute('ROLLBACK');raise


class CandidateUnavailable(ValueError):
    """A specific candidate changed or is no longer eligible; no intent exists."""


def _check_candidate_hashes(store,folder,record_sha):
    """Classify only proven immutable byte changes; I/O failures stay systemic."""
    folder=Path(folder)
    if not folder.is_absolute() or folder.is_symlink() or folder!=folder.resolve():raise ValueError('unsafe official evidence path')
    try:relative=folder.relative_to(store.root/'official-auto-evidence')
    except ValueError:raise ValueError('official evidence outside state root') from None
    if len(relative.parts)!=3 or relative.parts[-1]!=record_sha:raise ValueError('invalid official evidence identity')
    if any((store.root/'official-auto-evidence'/Path(*relative.parts[:i])).is_symlink() for i in range(4)):
        raise ValueError('unsafe official evidence directory')
    record_path=folder/'record.json'
    if record_path.is_symlink():raise ValueError('unsafe official record')
    raw=record_path.read_bytes()
    if len(raw)>2_000_000:raise ValueError('oversized official record')
    if hashlib.sha256(raw).hexdigest()!=record_sha:raise CandidateUnavailable('official record changed')
    record=strict_json(raw.decode('utf-8'));manifest=record.get('files')
    if not isinstance(manifest,dict) or not manifest:raise ValueError('official evidence manifest missing')
    for name,expected in manifest.items():
        part=Path(name)
        if part.is_absolute() or '..' in part.parts or not part.parts:raise ValueError('unsafe official evidence file')
        path=folder/part
        if any(p.is_symlink() for p in (path,*list(path.parents)[:len(part.parts)-1])):raise ValueError('unsafe official evidence file')
        if path.stat().st_size>20_000_000:raise ValueError('oversized official evidence file')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:raise CandidateUnavailable('official evidence file changed')


def reserve(store,article_id,day=None,slot=None):
    """Reserve through the shared quota, only after official enablement review."""
    if OFFICIAL_RESERVATION_ENABLED is not True: raise ValueError('official reservation disabled pending native integration')
    if slot not in SLOTS: raise ValueError('official reservation requires a known slot')
    _schema(store)
    row=store.db.execute('SELECT * FROM official_candidates WHERE article_id=?',(article_id,)).fetchone()
    if not row: raise CandidateUnavailable('official candidate missing')
    _check_candidate_hashes(store,row['evidence_path'],row['record_sha'])
    payload,value,policy=_validated(store,row['evidence_path'],row['record_sha'])
    publication=load_publish_policy()
    if publication.get('enabled') is not True or value['source']['id'] not in publication.get('source_ids',[]):
        raise ValueError('official publication policy disabled')
    if hashlib.sha256(payload).hexdigest()!=row['payload_sha'] or digest(policy)!=row['policy_sha']:
        raise CandidateUnavailable('official candidate changed')
    day=day or local_day();store.db.execute('BEGIN IMMEDIATE')
    try:
        if store.paused(): raise ValueError('paused')
        if store.db.execute("SELECT 1 FROM attempts WHERE state IN ('intent','unknown') LIMIT 1").fetchone(): raise ValueError('unresolved attempt requires reconciliation')
        count=store.db.execute("SELECT count(*) FROM attempts WHERE (state='published' AND published_day=?) OR (state IN ('intent','unknown') AND reserved_day=?)",(day,day)).fetchone()[0]
        if count>=4: raise ValueError('daily quota reached')
        current=store.db.execute('SELECT * FROM articles WHERE id=?',(article_id,)).fetchone()
        persisted=store.db.execute('SELECT * FROM official_candidates WHERE article_id=?',(article_id,)).fetchone()
        if not persisted or dict(persisted)!=dict(row): raise CandidateUnavailable('official candidate changed during reservation')
        if not current or current['state']!='official_ready' or current['source_url']!=value['source']['url'] or current['manifest_sha']!=row['record_sha']:
            raise CandidateUnavailable('official candidate not ready')
        _check_evidence(store,row['evidence_path'],row['record_sha'],payload,policy)
        if load_publish_policy()!=publication: raise ValueError('official publication policy changed during reservation')
        if slot is not None:store.db.execute('INSERT INTO slots VALUES (?,?)',(day,slot))
        cursor=store.db.execute("INSERT INTO attempts(article_id,reserved_day,state,created_at) VALUES (?,?,'intent',?)",(article_id,day,datetime.now(timezone.utc).isoformat()))
        store.db.execute('INSERT INTO official_attempt_slots VALUES (?,?,?)',(cursor.lastrowid,day,slot))
        store.db.execute("UPDATE articles SET state='publishing' WHERE id=?",(article_id,));store.db.execute('COMMIT')
        return cursor.lastrowid,dict(current)
    except BaseException:
        store.db.execute('ROLLBACK');raise


def release(store,attempt,note):
    """After external reconciliation, restore only an official candidate."""
    if not isinstance(note,str) or not note.strip(): raise ValueError('evidence note required')
    _schema(store);store.db.execute('BEGIN IMMEDIATE')
    try:
        row=store.db.execute("SELECT a.id FROM attempts t JOIN articles a ON a.id=t.article_id JOIN official_candidates o ON o.article_id=a.id WHERE t.id=? AND t.state IN ('intent','unknown')",(attempt,)).fetchone()
        if not row: raise ValueError('official attempt not pending')
        store.db.execute("UPDATE attempts SET state='released',note=? WHERE id=?",(note,attempt))
        store.db.execute("UPDATE articles SET state='official_ready' WHERE id=?",(row[0],));store.db.execute('COMMIT')
    except BaseException:
        store.db.execute('ROLLBACK');raise
