"""Candidate renderer: approved localized content plus optional bound illustrations."""
import hashlib,json,os,stat,logging
from pathlib import Path
from . import body_policy,preupload_journal
from .illustration_plan import _compose,bind_plan
from .localization import digest,load_approved
from .store import verify_snapshot,sha

class BodyRenderHold(ValueError):pass

def _asset(revision,path):
    revision=Path(revision);path=Path(path)
    if revision.is_symlink() or path.is_symlink() or path.parent.is_symlink() or path.parent.parent!=revision or not path.parent.name.startswith('generated-body-') or path.name not in ('image.png','image.jpg','image.webp'):raise BodyRenderHold('unsafe body asset path')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or not 0<st.st_size<=12_000_000:raise BodyRenderHold('invalid body asset')
        data=os.read(fd,12_000_001)
        if len(data)!=st.st_size:raise BodyRenderHold('body asset changed')
        return data
    finally:os.close(fd)

def _request_evidence(path,image_path,session,ordinal):
    path=Path(path);image_path=Path(image_path)
    if not isinstance(session,str) or len(session)!=32 or any(c not in '0123456789abcdef' for c in session) or type(ordinal) is not int or not 1<=ordinal<=5:raise BodyRenderHold('invalid upload identity')
    if path.is_symlink() or path.parent.is_symlink() or path.parent!=image_path.parent or path.name!='request-'+session+'-'+str(ordinal)+'.multipart':raise BodyRenderHold('unsafe request evidence')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or not 0<st.st_size<=20*1024*1024+32768:raise BodyRenderHold('invalid request evidence')
        data=os.read(fd,20*1024*1024+32769)
        if len(data)!=st.st_size:raise BodyRenderHold('request evidence changed')
        return data
    finally:os.close(fd)

def render(store,article,localized):
    """Validate each image; return approved blocks unchanged when none survive."""
    if load_approved(store,article)!=localized:raise BodyRenderHold('approved localization changed')
    vi=localized['locales']['Vietnamese'];en=localized['locales']['English'];original={'Vietnamese':vi['blocks'],'English':en['blocks']}
    body=article.get('body_preupload')
    if body is None:return original,[]
    # Snapshot and localization integrity remain mandatory for the article itself.
    folder=verify_snapshot(store.root,article['id']);meta=json.loads((folder/'metadata.json').read_text(encoding='utf-8'))
    manifest_sha=sha(folder/'snapshot-manifest.json');payload_sha=digest(localized)
    try:
        return _optional(store,article,body,vi,en,original,meta,manifest_sha,payload_sha)
    except Exception as exc:
        logging.getLogger(__name__).warning('Optional body illustrations omitted: %s',type(exc).__name__)
        if isinstance(body,dict) and isinstance(body.get('session_id'),str):
            try:
                identity=store.db.execute('SELECT article_id,manifest_sha,payload_sha FROM body_preupload_sessions WHERE session_id=?',(body['session_id'],)).fetchone()
                if identity is not None and tuple(identity)==(article['id'],manifest_sha,payload_sha):
                    rows=store.db.execute('SELECT ordinal FROM body_preuploads WHERE session_id=?',(body['session_id'],)).fetchall()
                    for row in rows:store.db.execute('INSERT OR IGNORE INTO body_preupload_decisions VALUES(?,?,?,?)',(body['session_id'],row[0],'omit','optional_bundle_unavailable'))
            except Exception:pass
        return original,[]

def _optional(store,article,body,vi,en,original,meta,manifest_sha,payload_sha):
    if not isinstance(body,dict) or set(body)!={'session_id','plan','uploads','policy_sha256'}:raise BodyRenderHold('invalid body bundle')
    if meta.get('content_origin')!='ai_original':raise BodyRenderHold('unsupported body source')
    _,policy_sha=body_policy.load_current()
    if body['policy_sha256']!=policy_sha or body['plan']!=bind_plan(vi['title'],vi['blocks'],manifest_sha,payload_sha):raise BodyRenderHold('plan or policy changed')
    if not isinstance(body['uploads'],list) or len(body['uploads'])>5:raise BodyRenderHold('invalid uploads')
    uploads=[];live={}
    for item in body['uploads']:
        if not isinstance(item,dict) or set(item)!={'ordinal','image_path','request_path','content_type','response','producer_request','producer_evidence'}:raise BodyRenderHold('invalid body upload')
        try:
            data=_asset(article['localization_revision'],item['image_path'])
            request_bytes=_request_evidence(item['request_path'],item['image_path'],body['session_id'],item['ordinal'])
        except Exception:continue  # journal.completed records the missing proof as omitted
        entry={k:v for k,v in item.items() if k not in ('image_path','request_path')};entry['image_bytes']=data;entry['request_bytes']=request_bytes
        if entry['ordinal'] in live:raise BodyRenderHold('duplicate body ordinal')
        live[entry['ordinal']]={k:entry[k] for k in ('request_bytes','content_type','response')};live[entry['ordinal']]['source_bytes']=data;uploads.append(entry)
    rows=preupload_journal.completed(store.db,body['session_id'],article['id'],manifest_sha,payload_sha,body['plan']['plan_sha256'],live_evidence=live)
    eligible={x['ordinal'] for x in rows if x['status']=='bound'};valid=[]
    def omit(n,reason):store.db.execute('INSERT OR IGNORE INTO body_preupload_decisions VALUES(?,?,?,?)',(body['session_id'],n,'omit',reason))
    seen_image_hashes=set()
    for entry in sorted(uploads,key=lambda x:x['ordinal']):
        if entry['ordinal'] not in eligible:continue
        image_hash=hashlib.sha256(entry['image_bytes']).hexdigest()
        if image_hash in seen_image_hashes:
            omit(entry['ordinal'],'duplicate_body_image');continue
        try:
            for blocks in (vi['blocks'],en['blocks']):_compose(vi['title'],vi['blocks'],body['plan'],manifest_sha,payload_sha,[entry],article_id=article['id'],policy_sha=policy_sha,target_blocks=blocks)
        except Exception:
            omit(entry['ordinal'],'body_validation_failed');continue
        valid.append(entry)
        seen_image_hashes.add(image_hash)
    if not valid:return original,[]
    try:
        rendered={name:_compose(vi['title'],vi['blocks'],body['plan'],manifest_sha,payload_sha,valid,article_id=article['id'],policy_sha=policy_sha,target_blocks=blocks) for name,blocks in original.items()}
    except Exception:
        for entry in valid:omit(entry['ordinal'],'body_revalidation_failed')
        return original,[]
    by_index={x['ordinal']:x['block_index'] for x in body['plan']['entries']}
    urls=[x['response']['imageUrl'] for x in sorted(valid,key=lambda x:(by_index[x['ordinal']],x['ordinal']))]
    return rendered,urls


def omit_all(store,article,localized,reason):
    """Record optional omissions only for the currently bound article session."""
    if load_approved(store,article)!=localized:raise BodyRenderHold('approved localization changed')
    body=article.get('body_preupload')
    if not isinstance(body,dict) or not isinstance(body.get('session_id'),str):return
    folder=verify_snapshot(store.root,article['id']);manifest_sha=sha(folder/'snapshot-manifest.json');payload_sha=digest(localized)
    row=store.db.execute('SELECT article_id,manifest_sha,payload_sha FROM body_preupload_sessions WHERE session_id=?',(body['session_id'],)).fetchone()
    if row is None or tuple(row)!=(article['id'],manifest_sha,payload_sha):return
    rows=store.db.execute('SELECT ordinal FROM body_preuploads WHERE session_id=?',(body['session_id'],)).fetchall()
    for item in rows:store.db.execute('INSERT OR IGNORE INTO body_preupload_decisions VALUES(?,?,?,?)',(body['session_id'],item[0],'omit',reason))
