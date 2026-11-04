"""Candidate worker client for a disposable pre-reservation uploader cgroup."""
import json,os,socket,stat,struct,time
from pathlib import Path

SOCKET=Path('/run/nailhub-body-preupload/uploader.sock')
MAX_MESSAGE=160_000
class UploadClientHold(ValueError):pass

def _socket_identity():
    path=SOCKET
    if path.is_symlink() or path.parent.is_symlink():raise UploadClientHold('unsafe socket')
    parent=path.parent.stat();st=path.lstat()
    if parent.st_uid!=0 or parent.st_mode&0o022 or not stat.S_ISDIR(parent.st_mode):raise UploadClientHold('untrusted socket parent')
    if not stat.S_ISSOCK(st.st_mode) or st.st_uid!=os.geteuid() or st.st_mode&0o007:raise UploadClientHold('untrusted uploader socket')
    return (st.st_dev,st.st_ino,st.st_mode,st.st_uid,st.st_gid)

def prepare(article,assets,day,slot,*,budget_seconds=60):
    if not assets:return None
    if type(budget_seconds) is not int or not 1<=budget_seconds<=60:raise UploadClientHold('invalid upload budget')
    revision=Path(article['localization_revision']).name
    start=time.monotonic();deadline=start+budget_seconds
    request={'deadline_ns':int(deadline*1_000_000_000),'article_id':article['id'],'revision_name':revision,'assets':assets,'day':day,'slot':slot,'budget_seconds':budget_seconds}
    data=json.dumps(request,ensure_ascii=False,separators=(',',':')).encode()+b'\n'
    if len(data)>MAX_MESSAGE:raise UploadClientHold('upload request too large')
    before=_socket_identity()
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as conn:
        # Server unit RuntimeMaxSec=75, KillMode=control-group: wait for its
        # bounded exit, not merely the per-upload deadline.
        end=start+80;conn.settimeout(max(0.001,end-time.monotonic()));conn.connect(str(SOCKET));pid,uid,gid=struct.unpack('3i',conn.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,struct.calcsize('3i')))
        if uid not in (0,os.geteuid()) or _socket_identity()!=before:raise UploadClientHold('untrusted uploader peer')
        left=end-time.monotonic()
        if left<=0:raise UploadClientHold('uploader deadline elapsed')
        conn.settimeout(left);conn.sendall(data);buf=bytearray()
        while b'\n' not in buf:
            left=end-time.monotonic()
            if left<=0:raise UploadClientHold('uploader deadline elapsed')
            conn.settimeout(left)
            chunk=conn.recv(min(4096,MAX_MESSAGE+1-len(buf)))
            if not chunk:raise UploadClientHold('incomplete uploader response')
            buf.extend(chunk)
            if len(buf)>MAX_MESSAGE:raise UploadClientHold('oversize uploader response')
        line,tail=bytes(buf).split(b'\n',1)
        if tail:raise UploadClientHold('extra uploader response')
    response=json.loads(line)
    if not isinstance(response,dict) or set(response)!={'status','bundle'} or response['status']!='COMPLETE' or not isinstance(response['bundle'],dict):raise UploadClientHold('uploader held')
    bundle=response['bundle']
    return bundle if bundle.get('session_id') else None
