"""Candidate one-shot pre-reservation uploader; socket activation, no Create."""
import json,os,re,socket,struct,time
from pathlib import Path
from .store import Store
from .localization import load_approved
from .body_preupload_native import run

ROOT=Path('/var/lib/nailhub-source-mirror')
MAX_REQUEST=160_000
class ServiceHold(ValueError):pass

def _pairs(pairs):
    out={}
    for k,v in pairs:
        if k in out:raise ServiceHold('duplicate key')
        out[k]=v
    return out

def receive(conn):
    buf=bytearray()
    while b'\n' not in buf:
        chunk=conn.recv(min(4096,MAX_REQUEST+1-len(buf)))
        if not chunk:raise ServiceHold('request incomplete')
        buf.extend(chunk)
        if len(buf)>MAX_REQUEST:raise ServiceHold('request too large')
    line,tail=bytes(buf).split(b'\n',1)
    if tail:raise ServiceHold('extra request')
    return json.loads(line,object_pairs_hook=_pairs)

def peer_connected(conn):
    import select
    readable,_,_=select.select([conn],[],[],0)
    if not readable:return True
    try:return bool(conn.recv(1,socket.MSG_PEEK|socket.MSG_DONTWAIT))
    except BlockingIOError:return True
    except OSError:return False

def handle(conn,*,worker_uid,root=ROOT,runner=run):
    pid,uid,gid=struct.unpack('3i',conn.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,struct.calcsize('3i')))
    if uid!=worker_uid:raise ServiceHold('wrong worker peer')
    value=receive(conn);keys={'deadline_ns','article_id','revision_name','assets','day','slot','budget_seconds'}
    if not isinstance(value,dict) or set(value)!=keys or not re.fullmatch('[a-f0-9]{64}',str(value['article_id'])) or not re.fullmatch(r'revision-[A-Za-z0-9-]+',str(value['revision_name'])) or not isinstance(value['assets'],list) or len(value['assets'])>5 or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',str(value['day'])) or value['slot'] not in (None,'08:00','12:00','16:00','20:00') or type(value['budget_seconds']) is not int or not 1<=value['budget_seconds']<=60:raise ServiceHold('invalid upload request')
    if type(value['deadline_ns']) is not int or value['deadline_ns']<=0:raise ServiceHold('invalid deadline')
    deadline=value['deadline_ns']/1_000_000_000
    if deadline<=time.monotonic() or deadline-time.monotonic()>60 or not peer_connected(conn):raise ServiceHold('expired or disconnected request')
    store=Store(root)
    try:
        folder=root/'articles'/value['article_id'];revision=folder/'localization'/value['revision_name'];article={'id':value['article_id'],'localization_revision':str(revision)}
        localized=load_approved(store,article)
        bundle=runner(store,article,localized,value['assets'],value['day'],value['slot'],deadline,peer_alive=lambda:peer_connected(conn))
        return (json.dumps({'status':'COMPLETE','bundle':bundle},ensure_ascii=False,separators=(',',':'))+'\n').encode()
    finally:store.close()

def main():
    # The worker holds its usual lock while invoking this service. The service
    # never creates a blog; all writes except one armed native image upload abort.
    worker_uid=os.geteuid()
    with socket.socket(fileno=3) as conn:
        conn.settimeout(70)
        try:raw=handle(conn,worker_uid=worker_uid)
        except Exception:raw=b'{"status":"HOLD"}\n'
        conn.sendall(raw)
if __name__=='__main__':main()
