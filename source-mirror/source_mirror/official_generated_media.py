"""AI media schema and provenance checks; no publication authority."""
import hashlib,json,re
from .localization import strict_json
from .ai_cover_stage import prompt_for
from .official_generated_rights import validate_production,TERMS,GOOGLE_TERMS
BASE={'id','sha256','provenance_url','credit','rights_basis','rights_evidence_sha256','alt'}

def is_generated(item):
    return isinstance(item,dict) and item.get('kind')=='generated'

def validate_descriptor(item):
    if set(item)!=BASE|{'kind'} or item.get('id')!='ai-cover' or item.get('provenance_url')!='https://antigravity.google/' or item.get('credit')!='AI-generated illustration' or item.get('rights_basis')!='generated-ai':raise ValueError('invalid generated descriptor')
    if any(not isinstance(item.get(k),str) or not item[k].strip() for k in item):raise ValueError('invalid generated descriptor fields')
    if any(not re.fullmatch(r'[0-9a-f]{64}',item[k]) for k in ('sha256','rights_evidence_sha256')):raise ValueError('invalid generated digests')

def check(item,image,rights,snapshot,draft_sha,payload_bytes,policy):
    validate_descriptor(item)
    if not isinstance(rights,bytes) or hashlib.sha256(rights).hexdigest()!=item['rights_evidence_sha256']:raise ValueError('generated evidence hash mismatch')
    e=strict_json(rights.decode('utf-8'))
    validate_production(e,image,policy)
    if item['sha256']!=e['image']['sha256']:raise ValueError('generated image mismatch')
    import io
    from PIL import Image
    try:
        with Image.open(io.BytesIO(image)) as picture:
            info=e['image']; expected={'JPEG':'image/jpeg','PNG':'image/png','WEBP':'image/webp'}
            if picture.format not in expected or info.get('mime')!=expected[picture.format] or picture.size!=(info.get('width'),info.get('height')) or picture.width<640 or picture.height<360 or picture.width*picture.height>25000000 or not 1.65<=picture.width/picture.height<=1.9 or getattr(picture,'n_frames',1)!=1:raise ValueError('generated image properties changed')
            picture.verify()
    except ValueError:raise
    except Exception:raise ValueError('invalid generated image') from None
    from .official_cover_subject import discover_official_cover
    source_id=snapshot['metadata'].get('source_id')
    if source_id not in policy.get('source_ids',[]) or discover_official_cover(snapshot) not in ('missing','unavailable','unusable'):raise ValueError('source not enabled or cover not established missing')
    source=snapshot['metadata'];payload=strict_json(payload_bytes.decode('utf-8'));locale=payload['locales']['en'];title=locale['title'];summary=locale['summary'];request=e.get('request')
    binding={'locale':'en','title':title,'summary':summary,'source_sha256':source['source_sha256'],'draft_sha256':draft_sha}
    subject_sha=hashlib.sha256(json.dumps(binding,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()
    if e.get('subject_sha256')!=subject_sha:raise ValueError('generated payload subject changed')
    if not isinstance(request,dict) or set(request)!={'version','source_id','source_sha256','draft_sha256','title','summary','nonce'} or request['version']!='official-cover-request-v3' or request['source_id']!=source_id or request['source_sha256']!=source['source_sha256'] or request['draft_sha256']!=draft_sha or request['title']!=title or request['summary']!=summary or not re.fullmatch(r'[0-9a-f]{64}',str(request['nonce'])):raise ValueError('generated request changed')
    request_sha=hashlib.sha256(json.dumps(request,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()
    if e.get('request_sha256')!=request_sha or e.get('prompt_sha256')!=hashlib.sha256(prompt_for(request['title'],request['summary']).encode()).hexdigest() or e.get('source_sha256')!=source['source_sha256'] or e.get('draft_sha256')!=draft_sha:raise ValueError('generated provenance mismatch')
    return e

# Provision separately; absence or expiry prevents generated publication evidence.
POLICY_PATH='/etc/nailhub-source-mirror/official-generated-media-policy.json'
def load_policy():
    import os,stat
    from pathlib import Path
    from datetime import datetime,timezone
    p=Path(POLICY_PATH)
    if os.name!='posix' or not p.is_absolute():raise ValueError('generated policy unavailable')
    for part in (p,*p.parents):
        st=part.lstat()
        if stat.S_ISLNK(st.st_mode) or st.st_uid!=0 or st.st_mode&0o022:raise ValueError('untrusted generated policy')
    if not p.is_file() or p.stat().st_size>16384:raise ValueError('invalid generated policy')
    policy=strict_json(p.read_text(encoding='utf-8'))
    if isinstance(policy,dict) and policy.get('version')=='official-generated-media-policy-v3': return _load_owner_grant_policy(policy)
    expected={'version','enabled','source_ids','provider','valid_until_utc','allowed_use','entitlement_sha256','provider_evidence_path','provider_evidence_sha256'}
    from .official_sources import SOURCES
    supported={item['id'] for item in SOURCES}
    if not isinstance(policy,dict) or set(policy)!=expected or policy['version']!='official-generated-media-policy-v2' or policy['enabled'] is not True or policy['provider']!='Antigravity subscription' or policy['allowed_use']!='editorial-publication' or not isinstance(policy['source_ids'],list) or not policy['source_ids'] or any(not isinstance(x,str) or x not in supported for x in policy['source_ids']) or len(set(policy['source_ids']))!=len(policy['source_ids']) or not re.fullmatch(r'[0-9a-f]{64}',str(policy['entitlement_sha256'])) or not re.fullmatch(r'[0-9a-f]{64}',str(policy['provider_evidence_sha256'])):raise ValueError('invalid generated policy')
    proof=Path(policy['provider_evidence_path']) if isinstance(policy['provider_evidence_path'],str) else None
    if proof is None or not proof.is_absolute():raise ValueError('provider facts unavailable')
    for part in (proof,*proof.parents):
        st=part.lstat()
        if stat.S_ISLNK(st.st_mode) or st.st_uid!=0 or st.st_mode&0o022:raise ValueError('untrusted provider facts')
    if not proof.is_file() or proof.stat().st_size>16384:raise ValueError('invalid provider facts')
    raw=proof.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=policy['provider_evidence_sha256']:raise ValueError('provider facts changed')
    facts=strict_json(raw.decode('utf-8'))
    if not isinstance(facts,dict) or set(facts)!={'provider','entitlement_sha256','terms_url','use'} or facts!={'provider':policy['provider'],'entitlement_sha256':policy['entitlement_sha256'],'terms_url':TERMS,'use':'editorial-publication'}:raise ValueError('provider facts mismatch')
    try:expiry=datetime.fromisoformat(policy['valid_until_utc'].replace('Z','+00:00'))
    except (KeyError,AttributeError,ValueError):raise ValueError('invalid generated expiry') from None
    if expiry.tzinfo is None or expiry<=datetime.now(timezone.utc):raise ValueError('generated policy expired')
    return policy


def _load_owner_grant_policy(policy,extra_source_ids=()):
    # Version 3 records an owner's explicit instruction; it does not attest provider terms.
    import os,stat
    from pathlib import Path
    from datetime import datetime,timezone
    from .official_sources import SOURCES
    fields={'version','enabled','source_ids','provider','valid_until_utc','allowed_use','authorization_path','authorization_sha256'}
    supported={x['id'] for x in SOURCES} | set(extra_source_ids)
    if (set(policy)!=fields or policy['enabled'] is not True or policy['provider']!='Antigravity'
        or policy['allowed_use']!='editorial-publication' or not isinstance(policy['source_ids'],list)
        or not policy['source_ids'] or any(not isinstance(x,str) or x not in supported for x in policy['source_ids'])
        or len(set(policy['source_ids']))!=len(policy['source_ids'])
        or not re.fullmatch(r'[0-9a-f]{64}',str(policy['authorization_sha256']))): raise ValueError('invalid generated policy')
    proof=Path(policy['authorization_path']) if isinstance(policy['authorization_path'],str) else None
    if proof is None or not proof.is_absolute(): raise ValueError('owner authorization unavailable')
    for part in (proof,*proof.parents):
        st=part.lstat()
        if stat.S_ISLNK(st.st_mode) or st.st_uid!=0 or st.st_mode&0o022:raise ValueError('untrusted owner authorization')
    if not proof.is_file() or proof.stat().st_size>16384:raise ValueError('invalid owner authorization')
    raw=proof.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=policy['authorization_sha256']:raise ValueError('owner authorization changed')
    facts=strict_json(raw.decode('utf-8'))
    if facts!={'version':'owner-authorization-v1','provider':policy['provider'],'allowed_use':policy['allowed_use'],'source_ids':policy['source_ids'],'authorization':'user-explicitly-authorized-ai-generated-editorial-covers'}:raise ValueError('owner authorization scope mismatch')
    try:expiry=datetime.fromisoformat(policy['valid_until_utc'].replace('Z','+00:00'))
    except (AttributeError,ValueError):raise ValueError('invalid generated expiry') from None
    if expiry.tzinfo is None or expiry<=datetime.now(timezone.utc):raise ValueError('generated policy expired')
    return policy
