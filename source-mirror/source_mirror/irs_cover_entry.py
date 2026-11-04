"""IRS pre-publication stage: cover only; downstream factual checks remain mandatory."""
import hashlib,json,os
from pathlib import Path
from . import irs_cover_ipc
from .ai_cover_stage import Hold

def stage_irs_cover(root,title,summary,cover_status,existing_cover=None,*,policy_path,source_id=None,source_sha256=None,draft_sha256=None):
 root=Path(root)
 if not root.is_absolute() or root.is_symlink() or root.resolve()!=root or not root.is_dir():raise Hold('invalid staging root')
 if cover_status=='valid':
  if not isinstance(existing_cover,bytes) or not existing_cover:raise Hold('cover missing')
  return {'status':'KEEP_EXISTING','sha256':hashlib.sha256(existing_cover).hexdigest(),'publication_enabled':False}
 if cover_status not in ('missing','unavailable','unusable'):raise Hold('cover status unknown')
 policy=irs_cover_ipc.load_policy(policy_path)
 image,evidence=irs_cover_ipc.request(policy,title,summary,source_id,source_sha256,draft_sha256)
 identity=evidence['image']['sha256']; folder=root/'generated-covers';folder.mkdir(mode=0o700,exist_ok=True)
 if folder.is_symlink():raise Hold('unsafe output')
 path=folder/(identity+'.image');fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'wb') as f:f.write(image);f.flush();os.fsync(f.fileno())
 record={'status':'GENERATED_WAITING_FACTUAL_MQM','image_sha256':identity,'prompt_sha256':evidence['prompt_sha256'],
         'evidence':evidence,'image':evidence['image'],'source_kind':'generated','model':evidence['model'],'publication_enabled':False}
 if cover_status!='missing':record['source_cover_status']=cover_status
 path=folder/(identity+'.json');fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'w') as f:json.dump(record,f,sort_keys=True);f.flush();os.fsync(f.fileno())
 return record
