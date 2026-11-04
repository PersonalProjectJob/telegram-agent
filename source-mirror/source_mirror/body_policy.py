"""Worker-side read-only validation for the dedicated body-image policy."""
import hashlib,json,os,stat
from datetime import datetime,timezone
from pathlib import Path

POLICY=Path('/etc/nailhub-source-mirror-body-image/policy.json')
class PolicyHold(ValueError):pass

def _read(path,limit=16384):
    path=Path(path)
    if not path.is_absolute():raise PolicyHold('invalid policy path')
    for part in (path,*path.parents):
        st=part.lstat()
        if stat.S_ISLNK(st.st_mode) or st.st_uid!=0 or st.st_mode&0o022:raise PolicyHold('untrusted policy path')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=0 or st.st_mode&0o022 or st.st_size>limit:raise PolicyHold('invalid policy file')
        raw=os.read(fd,limit+1)
        if not raw or len(raw)>limit:raise PolicyHold('invalid policy size')
        return raw
    finally:os.close(fd)

def load_current():
    raw=_read(POLICY)
    try:policy=json.loads(raw)
    except ValueError:raise PolicyHold('invalid policy JSON') from None
    keys={'version','enabled','source_ids','provider','valid_until_utc','allowed_use','worker_uid','broker_uid','brain','workspace','grant_path','grant_sha256'}
    if not isinstance(policy,dict) or set(policy)!=keys or policy['version']!='body-image-policy-v1' or policy['enabled'] is not True or policy['source_ids']!=['ai_original'] or policy['provider']!='Antigravity' or policy['allowed_use']!='editorial-inline' or any(type(policy[k]) is not int or policy[k]<=0 for k in ('worker_uid','broker_uid')):raise PolicyHold('invalid body policy')
    try:expiry=datetime.fromisoformat(policy['valid_until_utc'].replace('Z','+00:00'))
    except (ValueError,AttributeError):raise PolicyHold('invalid policy expiry') from None
    if expiry.tzinfo is None or expiry<=datetime.now(timezone.utc):raise PolicyHold('expired policy')
    try:grant=_read(policy['grant_path']);facts=json.loads(grant)
    except (ValueError,TypeError):raise PolicyHold('invalid grant') from None
    if hashlib.sha256(grant).hexdigest()!=policy['grant_sha256'] or facts!={'version':'owner-authorization-v1','authorization':'user-authorized-inline-editorial-illustrations','source_ids':['ai_original'],'provider':'Antigravity'}:raise PolicyHold('grant mismatch')
    return policy,hashlib.sha256(raw).hexdigest()
