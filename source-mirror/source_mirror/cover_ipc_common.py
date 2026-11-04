"""Shared framing, peer/socket checks and generated cover response validation."""
import base64,hashlib,io,json,socket,stat,struct
from pathlib import Path
from .ai_cover_stage import prompt_for, MODEL, Hold

def receive(sock,limit):
 out=bytearray()
 while b'\n' not in out:
  chunk=sock.recv(min(65536,limit+1-len(out)))
  if not chunk:raise Hold('incomplete IPC')
  out.extend(chunk)
  if len(out)>limit:raise Hold('oversize IPC')
 line,rest=bytes(out).split(b'\n',1)
 if rest:raise Hold('extra IPC')
 return json.loads(line)

def _peer(conn):return struct.unpack('3i',conn.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,struct.calcsize('3i')))

def _checked(path):
 st=path.lstat()
 if stat.S_ISLNK(st.st_mode):raise Hold('socket path symlink')
 return st

def _trusted_parent(policy):
 p=Path(policy['socket'])
 if not p.is_absolute() or '..' in p.parts:raise Hold('invalid socket path')
 parent=_checked(p.parent)
 if not stat.S_ISDIR(parent.st_mode) or parent.st_uid!=policy['broker_uid'] or parent.st_gid!=policy['socket_gid'] or parent.st_mode&0o022:raise Hold('untrusted socket directory')
 fingerprint=[(str(p.parent),parent.st_dev,parent.st_ino,parent.st_mode,parent.st_uid,parent.st_gid)]
 for part in p.parent.parents:
  v=_checked(part)
  if not stat.S_ISDIR(v.st_mode) or v.st_uid!=0 or v.st_gid!=0 or v.st_mode&0o022:raise Hold('untrusted socket ancestor')
  fingerprint.append((str(part),v.st_dev,v.st_ino,v.st_mode,v.st_uid,v.st_gid))
 return tuple(fingerprint)

def _trusted_socket(policy):
 p=Path(policy['socket']);parent=_trusted_parent(policy);st=_checked(p)
 if not stat.S_ISSOCK(st.st_mode) or st.st_uid!=policy['broker_uid'] or st.st_gid!=policy['socket_gid'] or st.st_mode&0o007:raise Hold('untrusted socket')
 return ((str(p),st.st_dev,st.st_ino,st.st_mode,st.st_uid,st.st_gid),)+parent

def validate_response(result,binding,title,summary,*,production_policy=None):
 if not isinstance(result,dict) or set(result)!={'status','evidence','image_b64','request_sha256'} or result['status']!='CANDIDATE_ONLY' or result['request_sha256']!=binding:raise Hold('broker failed or request mismatch')
 try:data=base64.b64decode(result['image_b64'],validate=True)
 except Exception:raise Hold('invalid image data') from None
 evidence=result['evidence']
 if not isinstance(evidence,dict):raise Hold('invalid evidence')
 info=evidence.get('image',{})
 if not isinstance(info,dict) or evidence.get('version')!='agy-generated-cover-v1' or evidence.get('kind')!='generated' or evidence.get('model')!=MODEL or evidence.get('prompt_sha256')!=hashlib.sha256(prompt_for(title,summary).encode()).hexdigest():raise Hold('generation evidence mismatch')
 if len(data)>12_000_000 or hashlib.sha256(data).hexdigest()!=info.get('sha256'):raise Hold('image mismatch')
 from .official_generated_rights import validate,validate_production,TERMS,GOOGLE_TERMS
 if production_policy is None:validate(evidence,data,{'version':'generated-rights-staging-v1','enabled':False,'antigravity_terms':TERMS,'google_terms':GOOGLE_TERMS,'use':'staging-review-only'})
 else:validate_production(evidence,data,production_policy)
 try:
  from PIL import Image
  with Image.open(io.BytesIO(data)) as im:
   if im.format not in ('JPEG','PNG','WEBP') or {'JPEG':'image/jpeg','PNG':'image/png','WEBP':'image/webp'}[im.format]!=info.get('mime') or list(im.size)!=[info.get('width'),info.get('height')] or im.width<640 or im.height<360 or im.width*im.height>30000000 or not 1.65<=im.width/im.height<=1.9:raise Hold('invalid image properties')
   im.verify()
  with Image.open(io.BytesIO(data)) as im:im.load()
 except Hold:raise
 except Exception:raise Hold('invalid image bytes') from None
 return data,evidence
