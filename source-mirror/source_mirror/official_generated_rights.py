"""Versioned generated-image evidence check for isolated staging only.

This is not a license or a publication authorization. Policy must be supplied
from a trusted, root-owned source before any worker can publish.
"""
import hashlib,re
TERMS='https://antigravity.google/terms'
GOOGLE_TERMS='https://policies.google.com/terms'
class GeneratedHold(ValueError):pass

def _hash(value):return isinstance(value,str) and re.fullmatch(r'[0-9a-f]{64}',value) is not None

def validate(evidence,image,policy):
 if not isinstance(policy,dict) or policy!={'version':'generated-rights-staging-v1','enabled':False,'antigravity_terms':TERMS,'google_terms':GOOGLE_TERMS,'use':'staging-review-only'}:
  raise GeneratedHold('untrusted or unsupported generated policy')
 if not isinstance(evidence,dict) or evidence.get('version')!='agy-generated-cover-v1' or evidence.get('kind')!='generated' or evidence.get('provider') not in ('Antigravity','Antigravity subscription'):raise GeneratedHold('invalid generated identity')
 if evidence.get('permission_scope')!='STAGING_ONLY_NOT_A_LICENSE' or evidence.get('publication_enabled') is not False:raise GeneratedHold('invalid permission scope')
 if evidence.get('visual_review')!='PENDING' or evidence.get('rights_review')!='PENDING':raise GeneratedHold('unreviewed evidence required')
 info=evidence.get('image')
 if not isinstance(info,dict) or not isinstance(image,bytes) or not image or len(image)>12000000:raise GeneratedHold('invalid image')
 if not _hash(evidence.get('prompt_sha256')) or not _hash(info.get('parent_session_sha256')) or not _hash(info.get('child_session_sha256')) or info['parent_session_sha256']==info['child_session_sha256']:raise GeneratedHold('missing provenance binding')
 if hashlib.sha256(image).hexdigest()!=info.get('sha256') or info.get('mime') not in ('image/jpeg','image/png','image/webp'):raise GeneratedHold('image binding mismatch')
 if evidence.get('model')!='gemini-3.8-flash-medium':raise GeneratedHold('unexpected model')
 return {'status':'GENERATED_STAGING_ONLY','publication_enabled':False,'image_sha256':info['sha256']}


def validate_production(evidence,image,policy):
    """Evidence must originate in production mode under the exact policy."""
    from .localization import digest
    if not isinstance(policy,dict) or policy.get('version') not in ('official-generated-media-policy-v2','official-generated-media-policy-v3') or policy.get('enabled') is not True or policy.get('allowed_use')!='editorial-publication' or policy.get('provider')!=('Antigravity' if policy.get('version')=='official-generated-media-policy-v3' else 'Antigravity subscription') :raise GeneratedHold('production policy unavailable')
    if not isinstance(evidence,dict) or evidence.get('provider')!=policy.get('provider') or evidence.get('permission_scope')!='PRODUCTION_AUTHORIZED_BY_POLICY' or evidence.get('publication_enabled') is not True or evidence.get('authorization_policy_sha256')!=digest(policy):raise GeneratedHold('staging evidence cannot authorize publication')
    # Reuse image/session invariants; policy state is deliberately not converted
    # into evidence. This local structural copy is never returned as authority.
    structural=dict(evidence,permission_scope='STAGING_ONLY_NOT_A_LICENSE',publication_enabled=False,visual_review='PENDING',rights_review='PENDING')
    validate(structural,image,{'version':'generated-rights-staging-v1','enabled':False,'antigravity_terms':TERMS,'google_terms':GOOGLE_TERMS,'use':'staging-review-only'})
    return {'status':'PRODUCTION_EVIDENCE_VALID','image_sha256':evidence['image']['sha256']}
