"""Candidate worker-side body illustration generation client; no Create calls."""
import base64,hashlib,io,json,os,secrets,socket,stat,struct,tempfile,time
from pathlib import Path
from PIL import Image
from . import body_policy,budget
from .illustration_plan import bind_plan,make_request
from .localization import digest,load_approved
from .store import verify_snapshot,sha
from .verify_body_signature import verify_trusted

SOCKET=Path('/run/nailhub-body-image/producer.sock')
MAX_REPLY=18_000_000
class BodyClientHold(ValueError):pass

def _trusted_socket(policy):
    path=SOCKET
    if path.is_symlink() or path.parent.is_symlink():raise BodyClientHold('unsafe socket')
    parent=path.parent.stat();st=path.lstat()
    if parent.st_uid!=0 or parent.st_mode&0o022 or not stat.S_ISDIR(parent.st_mode):raise BodyClientHold('untrusted socket parent')
    if not stat.S_ISSOCK(st.st_mode) or st.st_uid!=policy['broker_uid'] or st.st_mode&0o007:raise BodyClientHold('untrusted socket')
    return (st.st_dev,st.st_ino,st.st_mode,st.st_uid,st.st_gid)

def _read(sock,deadline):
    raw=bytearray()
    while b'\n' not in raw:
        left=deadline-time.monotonic()
        if left<=0:raise BodyClientHold('body response deadline elapsed')
        sock.settimeout(left)
        chunk=sock.recv(min(65536,MAX_REPLY+1-len(raw)))
        if not chunk:raise BodyClientHold('incomplete response')
        raw.extend(chunk)
        if len(raw)>MAX_REPLY:raise BodyClientHold('oversize response')
    line,tail=bytes(raw).split(b'\n',1)
    if tail:raise BodyClientHold('extra response')
    return json.loads(line)

def request_image(policy,policy_sha,item,article_id,manifest_sha,payload_sha,plan_sha,timeout):
    seconds=budget.socket_budget(timeout)
    if seconds is None:raise BodyClientHold('insufficient body budget')
    nonce=secrets.token_hex(32);expected=make_request(item,article_id,manifest_sha,payload_sha,plan_sha,policy_sha,nonce)
    end=time.monotonic()+seconds
    value={'deadline_ns':int(end*1_000_000_000),'item':item,'article_id':article_id,'manifest_sha':manifest_sha,'payload_sha':payload_sha,'plan_sha':plan_sha,'nonce':nonce,'policy_sha':policy_sha,'budget_seconds':seconds}
    before=_trusted_socket(policy)
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as conn:
        conn.settimeout(max(0.001,end-time.monotonic()));conn.connect(str(SOCKET));pid,uid,gid=struct.unpack('3i',conn.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,struct.calcsize('3i')))
        # Socket activation may expose PID 1/root before handing fd to broker;
        # authenticity is always enforced by a fixed-key producer signature.
        if uid not in (0,policy['broker_uid']) or _trusted_socket(policy)!=before:raise BodyClientHold('untrusted socket peer')
        left=end-time.monotonic()
        if left<=0:raise BodyClientHold('body request deadline elapsed')
        conn.settimeout(left);conn.sendall(json.dumps(value,separators=(',',':')).encode()+b'\n');response=_read(conn,end)
    if not isinstance(response,dict) or set(response)!={'status','request','evidence','image_b64'} or response['status']!='CANDIDATE_ONLY' or response['request']!=expected:raise BodyClientHold('producer response mismatch')
    try:data=base64.b64decode(response['image_b64'],validate=True)
    except ValueError:raise BodyClientHold('invalid producer image') from None
    verify_trusted(expected,data,response['evidence'])
    try:
        with Image.open(io.BytesIO(data)) as im:
            ext={'PNG':'.png','JPEG':'.jpg','WEBP':'.webp'}.get(im.format)
            if not ext or im.width<640 or im.height<360 or im.width*im.height>30_000_000:raise BodyClientHold('invalid image dimensions')
            im.verify()
        with Image.open(io.BytesIO(data)) as im:im.load()
    except BodyClientHold:raise
    except Exception as exc:raise BodyClientHold('invalid image bytes') from exc
    return {'ordinal':item['ordinal'],'image_bytes':data,'evidence':{'ordinal':item['ordinal'],'image_sha256':hashlib.sha256(data).hexdigest(),'producer_request':expected,'producer_evidence':response['evidence'],'extension':ext}}

def generate(store,article,localized,*,total_seconds=360):
    """Generate optional assets before reserve; missing policy/broker yields no assets."""
    folder=verify_snapshot(store.root,article['id']);meta=json.loads((folder/'metadata.json').read_text(encoding='utf-8'))
    if meta.get('content_origin')!='ai_original':return None
    if load_approved(store,article)!=localized:raise BodyClientHold('localization changed')
    revision=Path(article['localization_revision'])
    if revision.is_symlink() or revision.parent!=folder/'localization':raise BodyClientHold('unsafe revision')
    try:
        policy,policy_sha=body_policy.load_current();manifest_sha=sha(folder/'snapshot-manifest.json');payload_sha=digest(localized);vi=localized['locales']['Vietnamese'];plan=bind_plan(vi['title'],vi['blocks'],manifest_sha,payload_sha)
        def one(item,seconds):return request_image(policy,policy_sha,item,article['id'],manifest_sha,payload_sha,plan['plan_sha256'],seconds)
        outcome=budget.collect(plan['entries'],one,total_seconds=total_seconds)
        assets=[]
        for result in outcome['images']:
            info=result['evidence'];directory=Path(tempfile.mkdtemp(prefix='generated-body-',dir=revision));os.chmod(directory,0o700);image=directory/('image'+info['extension']);fd=os.open(image,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'wb') as out:out.write(result['image_bytes']);out.flush();os.fsync(out.fileno())
            evidence={'request':info['producer_request'],'evidence':info['producer_evidence'],'image_sha256':info['image_sha256']};(directory/'evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,sort_keys=True),encoding='utf-8');os.chmod(directory/'evidence.json',0o600)
            assets.append({'ordinal':result['ordinal'],'image_path':str(image),'producer_request':info['producer_request'],'producer_evidence':info['producer_evidence']})
        return {'plan':plan,'assets':assets,'policy_sha256':policy_sha,'omitted':outcome['omitted']}
    except Exception as exc:
        print('optional body generation omitted: '+type(exc).__name__,flush=True);return None
