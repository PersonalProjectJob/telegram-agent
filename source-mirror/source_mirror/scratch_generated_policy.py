"""Trusted v2 provider entitlement, explicitly scoped to Scratch."""
import hashlib,os,re,stat
from datetime import datetime,timezone
from pathlib import Path
from .localization import strict_json
from .official_generated_rights import TERMS

def _trusted(path):
 p=Path(path)
 if os.name!='posix' or not p.is_absolute(): raise ValueError('policy path unavailable')
 for part in (p,*p.parents):
  st=part.lstat()
  if stat.S_ISLNK(st.st_mode) or st.st_uid!=0 or st.st_mode&0o022: raise ValueError('untrusted policy path')
 if not p.is_file() or p.stat().st_size>16384: raise ValueError('invalid policy file')
 return p

def load_policy(path):
 p=_trusted(path);policy=strict_json(p.read_text(encoding='utf8'))
 if isinstance(policy,dict) and policy.get('version')=='official-generated-media-policy-v3':
  if policy.get('source_ids') not in (['scratch'],['scratch','ai_original']):raise ValueError('generated scope unavailable')
  from .official_generated_media import _load_owner_grant_policy
  return _load_owner_grant_policy(policy,extra_source_ids=('scratch','ai_original'))
 fields={'version','enabled','source_ids','provider','valid_until_utc','allowed_use','entitlement_sha256','provider_evidence_path','provider_evidence_sha256'}
 if not isinstance(policy,dict) or set(policy)!=fields or policy['version']!='official-generated-media-policy-v2' or policy['enabled'] is not True or policy['provider']!='Antigravity subscription' or policy['allowed_use']!='editorial-publication' or policy['source_ids']!=['scratch']: raise ValueError('Scratch generated policy unavailable')
 for key in ('entitlement_sha256','provider_evidence_sha256'):
  if not isinstance(policy[key],str) or not re.fullmatch(r'[0-9a-f]{64}',policy[key]):raise ValueError('invalid evidence hash')
 proof=_trusted(policy['provider_evidence_path']);raw=proof.read_bytes()
 if hashlib.sha256(raw).hexdigest()!=policy['provider_evidence_sha256']:raise ValueError('provider evidence changed')
 expected={'provider':policy['provider'],'entitlement_sha256':policy['entitlement_sha256'],'terms_url':TERMS,'use':'editorial-publication'}
 if strict_json(raw.decode('utf8'))!=expected:raise ValueError('provider evidence mismatch')
 try: expiry=datetime.fromisoformat(policy['valid_until_utc'].replace('Z','+00:00'))
 except (ValueError,AttributeError,TypeError):raise ValueError('invalid policy expiry') from None
 if expiry.tzinfo is None or expiry<=datetime.now(timezone.utc):raise ValueError('expired policy')
 return policy
