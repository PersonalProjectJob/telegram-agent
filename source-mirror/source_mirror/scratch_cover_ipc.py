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
 base={'version','socket','broker_uid','broker_gid','worker_uid','worker_gid','socket_gid','workspace','brain'}
 production=policy.get('version')=='scratch-ai-ipc-production-v2'
 if set(policy)!=(base | ({'generated_policy_path'} if production else set())) or policy.get('version') not in ('scratch-ai-ipc-stage-v2','scratch-ai-ipc-production-v2'):raise Hold('invalid policy')
 if production and (not isinstance(policy['generated_policy_path'],str) or not Path(policy['generated_policy_path']).is_absolute()):raise Hold('invalid generated policy path')
 if any(type(policy[k]) is not int or policy[k]<=0 for k in ('broker_uid','broker_gid','worker_uid','worker_gid','socket_gid')):raise Hold('invalid uid')
 for k in ('socket','workspace','brain'):
  if not isinstance(policy[k],str) or not Path(policy[k]).is_absolute():raise Hold('invalid path')
 return policy






def validate_request(req):
 if not isinstance(req,dict) or set(req)!={'version','source_id','article_id','source_manifest_sha','payload_sha','title','summary','nonce'} or req['version']!='scratch-cover-request-v1' or req['source_id'] not in ('scratch','ai_original'): raise Hold('invalid request')
 for key in ('article_id','source_manifest_sha','payload_sha','nonce'):
  value=req[key]
  if not isinstance(value,str) or len(value)!=64 or any(c not in '0123456789abcdef' for c in value): raise Hold('invalid request binding')
 prompt_for(req['title'],req['summary'])

def request(policy,title,summary,article_id,source_manifest_sha,payload_sha,*,source_id='scratch'):
 p=Path(policy['socket']);before=_trusted_socket(policy)
 with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
  s.settimeout(540);s.connect(str(p))
  pid,uid,gid=_peer(s)
  if uid!=policy['broker_uid'] or gid!=policy['broker_gid']:raise Hold('wrong broker credentials')
  if _trusted_socket(policy)!=before:raise Hold('socket path changed during connect')
  nonce=secrets.token_hex(32)
  req={'version':'scratch-cover-request-v1','source_id':source_id,'article_id':article_id,'source_manifest_sha':source_manifest_sha,'payload_sha':payload_sha,'title':title,'summary':summary,'nonce':nonce}
  validate_request(req)
  binding=hashlib.sha256(json.dumps(req,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()
  s.sendall(json.dumps(req).encode()+b'\n')
  result=receive(s,MAX_REPLY)
 production_policy=None
 if policy['version']=='scratch-ai-ipc-production-v2':
  from .scratch_generated_policy import load_policy as load_generated_policy
  production_policy=load_generated_policy(policy['generated_policy_path'])
  if source_id not in production_policy['source_ids']:raise Hold('source not authorized')
 data,evidence=validate_response(result,binding,title,summary,production_policy=production_policy)
 evidence=dict(evidence);evidence['request']=req;evidence['request_sha256']=binding
 return data,evidence


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
     validate_request(req)
     binding=hashlib.sha256(json.dumps(req,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()
     with tempfile.TemporaryDirectory(prefix='irs-image-',dir=policy['workspace']) as folder:
      publication_policy=None
      policy_loader=None
      if policy['version']=='scratch-ai-ipc-production-v2':
       from .scratch_generated_policy import load_policy as load_generated_policy
       def policy_loader():
        current=load_generated_policy(policy['generated_policy_path'])
        if req['source_id'] not in current['source_ids']:raise Hold('source not authorized')
        return current
       publication_policy=policy_loader()
      from .ai_cover_stage import NoGeneratedImage
      try:evidence=run(req['title'],req['summary'],Path(folder),Path(policy['brain']),publication_policy=publication_policy,policy_loader=policy_loader)
      except NoGeneratedImage:evidence=run(req['title'],req['summary'],Path(folder),Path(policy['brain']),publication_policy=publication_policy,policy_loader=policy_loader)
      image=(Path(folder)/evidence['file']).read_bytes()
      response={'status':'CANDIDATE_ONLY','evidence':evidence,'image_b64':base64.b64encode(image).decode('ascii'),'request_sha256':binding}
    except Exception as exc:
     print('IPC_HOLD',type(exc).__name__,str(exc) if isinstance(exc,Hold) else '',flush=True);response={'status':'HOLD'}
    try:conn.sendall(json.dumps(response,separators=(',',':')).encode()+b'\n')
    except OSError:pass
