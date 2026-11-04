"""One-connection body producer candidate for socket activation; not installed."""
import base64,hashlib,json,os,socket,stat,struct,time
from datetime import datetime,timezone
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from .body_producer import request,invoke,extract_image,sign_evidence,ProducerHold
from .illustration_plan import PlanError
from .cover_priority import cover_busy

POLICY=Path('/etc/nailhub-source-mirror-body-image/policy.json')
PRIVATE_KEY=Path('/etc/nailhub-source-mirror-body-image/private-ed25519.pem')
MAX_REQUEST=16384
MAX_RESPONSE=18_000_000

class ServiceHold(ValueError):pass

def _trusted_root_file(path,max_size):
    path=Path(path)
    if not path.is_absolute():raise ServiceHold('path must be absolute')
    for part in (path,*path.parents):
        st=part.lstat()
        if stat.S_ISLNK(st.st_mode) or st.st_uid!=0 or st.st_mode&0o022:raise ServiceHold('untrusted policy path')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=0 or st.st_mode&0o022 or st.st_size>max_size:raise ServiceHold('untrusted policy')
        data=os.read(fd,max_size+1)
        if not data or len(data)>max_size:raise ServiceHold('invalid policy size')
        return data
    finally:os.close(fd)

def load_policy():
    raw=_trusted_root_file(POLICY,16384)
    try:policy=json.loads(raw)
    except ValueError:raise ServiceHold('invalid policy') from None
    keys={'version','enabled','source_ids','provider','valid_until_utc','allowed_use','worker_uid','broker_uid','brain','workspace','grant_path','grant_sha256'}
    if not isinstance(policy,dict) or set(policy)!=keys or policy['version']!='body-image-policy-v1' or policy['enabled'] is not True or policy['source_ids']!=['ai_original'] or policy['provider']!='Antigravity' or policy['allowed_use']!='editorial-inline' or any(type(policy[k]) is not int or policy[k]<=0 for k in ('worker_uid','broker_uid')):raise ServiceHold('invalid policy fields')
    try:expiry=datetime.fromisoformat(policy['valid_until_utc'].replace('Z','+00:00'))
    except (ValueError,AttributeError):raise ServiceHold('invalid expiry') from None
    if expiry.tzinfo is None or expiry<=datetime.now(timezone.utc):raise ServiceHold('expired policy')
    try:grant_raw=_trusted_root_file(policy['grant_path'],16384);grant=json.loads(grant_raw)
    except (ValueError,TypeError):raise ServiceHold('invalid grant') from None
    if hashlib.sha256(grant_raw).hexdigest()!=policy['grant_sha256'] or grant!={'version':'owner-authorization-v1','authorization':'user-authorized-inline-editorial-illustrations','source_ids':['ai_original'],'provider':'Antigravity'}:raise ServiceHold('body grant mismatch')
    return policy,hashlib.sha256(raw).hexdigest()

def load_private_key(broker_uid):
    path=PRIVATE_KEY
    for part in path.parents:
        st=part.lstat()
        if stat.S_ISLNK(st.st_mode) or st.st_uid!=0 or st.st_mode&0o022:raise ServiceHold('untrusted key parent')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=broker_uid or stat.S_IMODE(st.st_mode)!=0o600 or st.st_size>4096:raise ServiceHold('invalid private key permissions')
        data=os.read(fd,4097)
    finally:os.close(fd)
    try:key=serialization.load_pem_private_key(data,password=None)
    except ValueError:raise ServiceHold('invalid private key') from None
    if not isinstance(key,Ed25519PrivateKey):raise ServiceHold('invalid key algorithm')
    return key

def _pairs(pairs):
    out={}
    for key,value in pairs:
        if key in out:raise ServiceHold('duplicate JSON key')
        out[key]=value
    return out

def receive(sock):
    out=bytearray()
    while b'\n' not in out:
        chunk=sock.recv(min(4096,MAX_REQUEST+1-len(out)))
        if not chunk:raise ServiceHold('incomplete request')
        out.extend(chunk)
        if len(out)>MAX_REQUEST:raise ServiceHold('oversize request')
    line,tail=bytes(out).split(b'\n',1)
    if tail:raise ServiceHold('extra request')
    try:return json.loads(line,object_pairs_hook=_pairs)
    except ValueError:raise ServiceHold('invalid JSON') from None

def peer_connected(conn):
    import select
    readable,_,_=select.select([conn],[],[],0)
    if not readable:return True
    try:return bool(conn.recv(1,socket.MSG_PEEK|socket.MSG_DONTWAIT))
    except BlockingIOError:return True
    except OSError:return False

def handle(conn,policy,policy_sha,key,*,generator=invoke,extractor=extract_image,clock=time.monotonic):
    """Peer/nonce/request binding: only accepts worker data, never shell commands."""
    pid,uid,gid=struct.unpack('3i',conn.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,struct.calcsize('3i')))
    if uid!=policy['worker_uid']:raise ServiceHold('invalid worker peer')
    value=receive(conn)
    fields={'deadline_ns','item','article_id','manifest_sha','payload_sha','plan_sha','nonce','policy_sha','budget_seconds'}
    if not isinstance(value,dict) or set(value)!=fields or value['policy_sha']!=policy_sha or type(value['budget_seconds']) is not int or not 1<=value['budget_seconds']<=65:raise ServiceHold('invalid request')
    if type(value['deadline_ns']) is not int or value['deadline_ns']<=0:raise ServiceHold('invalid absolute deadline')
    deadline=value['deadline_ns']/1_000_000_000
    if deadline<=clock() or deadline-clock()>65 or not peer_connected(conn):raise ServiceHold('expired or disconnected request')
    req=request(value['item'],value['article_id'],value['manifest_sha'],value['payload_sha'],value['plan_sha'],policy_sha,value['nonce'])
    start=clock();budget=min(value['budget_seconds'],deadline-start)
    # Workspace must be created by the service, never supplied by the request.
    import tempfile
    base=Path(policy['workspace'])
    if not base.is_absolute() or base.is_symlink() or base.resolve()!=base or not base.is_dir():raise ServiceHold('unsafe workspace')
    st=base.stat()
    if st.st_uid!=policy['broker_uid'] or stat.S_IMODE(st.st_mode)&0o077:raise ServiceHold('untrusted workspace owner or permissions')
    with tempfile.TemporaryDirectory(prefix='body-image-',dir=base) as folder:
        if cover_busy() or clock()>=deadline or not peer_connected(conn):raise ServiceHold('cover busy or request expired')
        result=generator(req,Path(folder),timeout=budget,clock=clock,guard=lambda:clock()<deadline and peer_connected(conn) and not cover_busy())
        remaining=budget-(clock()-start)
        if remaining<=0 or not peer_connected(conn) or cover_busy():raise ServiceHold('generation budget elapsed or cover busy')
        image,info=extractor(result['parent'],Path(policy['brain']),timeout=min(20,remaining),clock=clock)
        if clock()-start>=budget:raise ServiceHold('extraction budget elapsed')
        evidence=sign_evidence(key,req,image,{k:info[k] for k in ('parent_session_sha256','child_session_sha256')})
        response={'status':'CANDIDATE_ONLY','request':req,'evidence':evidence,'image_b64':base64.b64encode(image).decode('ascii')}
        raw=json.dumps(response,separators=(',',':')).encode()+b'\n'
        if len(raw)>MAX_RESPONSE:raise ServiceHold('response too large')
        return raw

def main():
    # Socket-activated per-connection systemd service. RuntimeMaxSec and KillMode
    # must be proven under a real isolated cgroup before deployment.
    policy,policy_sha=load_policy()
    if os.geteuid()!=policy['broker_uid']:raise ServiceHold('wrong service identity')
    key=load_private_key(policy['broker_uid'])
    with socket.socket(fileno=3) as conn:
        conn.settimeout(70)
        try:response=handle(conn,policy,policy_sha,key)
        except (ServiceHold,ProducerHold,PlanError,ValueError,OSError):response=b'{"status":"HOLD"}\n'
        conn.sendall(response)

if __name__=='__main__':main()
