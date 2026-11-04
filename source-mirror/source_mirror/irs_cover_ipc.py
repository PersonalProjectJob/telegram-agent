"""Isolated Unix socket image IPC. This is NOT a publish authorization."""
import base64,hashlib,json,os,socket,stat,struct,tempfile,secrets,io
from .ai_cover_stage import prompt_for, MODEL
from pathlib import Path
from .ai_cover_stage import run,Hold
from .cover_ipc_common import receive, _peer, _checked, _trusted_parent, _trusted_socket, validate_response
MAX_REPLY=18_000_000
MAX_REQ=8000


def load_policy(path):
 p=Path(path)
 if not p.is_absolute():raise Hold('invalid policy')
 for x in (p,*p.parents):
  s=x.lstat()
  if stat.S_ISLNK(s.st_mode) or s.st_uid!=0 or s.st_gid!=0 or s.st_mode&0o022:raise Hold('untrusted policy')
 if p.stat().st_size>16384:raise Hold('policy too big')
 policy=json.loads(p.read_text())
 if set(policy)!={'version','socket','broker_uid','broker_gid','worker_uid','worker_gid','socket_gid','source_ids','workspace','brain'} or policy['version'] not in ('official-ai-ipc-stage-v3','official-ai-ipc-production-v3'):raise Hold('invalid policy')
 from .official_sources import SOURCES
 if not isinstance(policy['source_ids'],list) or not policy['source_ids'] or len(set(policy['source_ids']))!=len(policy['source_ids']) or any(x not in {item['id'] for item in SOURCES} for x in policy['source_ids']):raise Hold('invalid source policy')
 if any(type(policy[k]) is not int or policy[k]<=0 for k in ('broker_uid','broker_gid','worker_uid','worker_gid','socket_gid')):raise Hold('invalid uid')
 for k in ('socket','workspace','brain'):
  if not isinstance(policy[k],str) or not Path(policy[k]).is_absolute():raise Hold('invalid path')
 return policy


def validate_request(req,policy):
 from .official_sources import SOURCES
 if not isinstance(req,dict) or set(req)!={'version','source_id','source_sha256','draft_sha256','title','summary','nonce'} or req['version']!='official-cover-request-v3':raise Hold('invalid request')
 if req['source_id'] not in {x['id'] for x in SOURCES} or req['source_id'] not in policy.get('source_ids',[]):raise Hold('source not enabled')
 for key in ('source_sha256','draft_sha256','nonce'):
  value=req[key]
  if not isinstance(value,str) or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):raise Hold('invalid request binding')
 prompt_for(req['title'],req['summary'])




def request(policy,title,summary,source_id,source_sha256,draft_sha256):
 p=Path(policy['socket']);before=_trusted_socket(policy)
 with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
  s.settimeout(540);s.connect(str(p))
  pid,uid,gid=_peer(s)
  if uid!=policy['broker_uid'] or gid!=policy['broker_gid']:raise Hold('wrong broker credentials')
  if _trusted_socket(policy)!=before:raise Hold('socket path changed during connect')
  nonce=secrets.token_hex(32)
  req={'version':'official-cover-request-v3','source_id':source_id,'source_sha256':source_sha256,'draft_sha256':draft_sha256,'title':title,'summary':summary,'nonce':nonce}
  validate_request(req,policy)
  binding=hashlib.sha256(json.dumps(req,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()
  s.sendall(json.dumps(req).encode()+b'\n')
  result=receive(s,MAX_REPLY)
 production_policy=None
 if policy['version']=='official-ai-ipc-production-v3':
  from .official_generated_media import load_policy as load_generated_policy
  production_policy=load_generated_policy()
  if source_id not in production_policy['source_ids']:raise Hold('production source not enabled')
 data,evidence=validate_response(result,binding,title,summary,production_policy=production_policy)
 evidence=dict(evidence);evidence['request']=req;evidence['request_sha256']=binding
 return data,evidence



def run_with_one_missing_retry(title,summary,folder,brain,publication_policy):
 from .ai_cover_stage import NoGeneratedImage
 try:return run(title,summary,folder,brain,publication_policy=publication_policy)
 except NoGeneratedImage:return run(title,summary,folder,brain,publication_policy=publication_policy)

def serve(policy):
 if os.geteuid()!=policy['broker_uid'] or os.getegid()!=policy['broker_gid']:raise Hold('wrong broker process credentials')
 path=Path(policy['socket'])
 before=_trusted_parent(policy)
 if path.exists() or path.is_symlink():raise Hold('socket exists')
 with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as server:
  server.bind(str(path));os.chown(path,-1,policy['socket_gid']);os.chmod(path,0o660)
  if _trusted_parent(policy)!=before:raise Hold('socket parent changed during bind')
  _trusted_socket(policy);server.listen(2)
  while True:
   conn,_=server.accept()
   with conn:
    try:
     conn.settimeout(240)
     pid,uid,gid=_peer(conn)
     if uid!=policy['worker_uid'] or gid!=policy['worker_gid']:raise Hold('wrong worker credentials')
     req=receive(conn,MAX_REQ)
     validate_request(req,policy)
     binding=hashlib.sha256(json.dumps(req,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()
     with tempfile.TemporaryDirectory(prefix='irs-image-',dir=policy['workspace']) as folder:
      publication_policy=None
      if policy['version']=='official-ai-ipc-production-v3':
       from .official_generated_media import load_policy as load_generated_policy
       publication_policy=load_generated_policy()
       if req['source_id'] not in publication_policy['source_ids']:raise Hold('production source not enabled')
      evidence=run_with_one_missing_retry(req['title'],req['summary'],Path(folder),Path(policy['brain']),publication_policy)
      image=(Path(folder)/evidence['file']).read_bytes()
      response={'status':'CANDIDATE_ONLY','evidence':evidence,'image_b64':base64.b64encode(image).decode('ascii'),'request_sha256':binding}
    except Exception as exc:
     print('IPC_HOLD',type(exc).__name__,str(exc) if isinstance(exc,Hold) else '',flush=True);response={'status':'HOLD'}
    try:conn.sendall(json.dumps(response,separators=(',',':')).encode()+b'\n')
    except OSError:pass
