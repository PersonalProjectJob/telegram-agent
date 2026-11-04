"""Review-only contract prototype. It does not authorize or create publications.

Authenticators and trusted policy installation are not implemented. No code in
the publisher calls this module. A caller must never treat its return as a
publication permission; the .gov publication gate remains closed.
"""
from datetime import datetime, timezone
import hashlib
import json
from urllib.parse import urlsplit

from .official_sources import SOURCES
from .official_draft import LABELS
from .official_payload import build as build_payload
from .localization import strict_json

VERSION = 'official-publication-contract-v1'


def digest_bytes(value):
    if not isinstance(value, bytes) or not value:
        raise ValueError('nonempty bytes required')
    return hashlib.sha256(value).hexdigest()


def digest_object(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _https(url):
    if not isinstance(url, str): raise ValueError('invalid evidence URL')
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('invalid evidence URL')


def validate_contract(contract, *, source_bytes, draft_bytes, payload_bytes, media_bytes,
                      rights_evidence_bytes, policy, trusted_policy_sha256, authenticate, now=None):
    """Validate prospective bindings using an external trusted authenticator.

    ``authenticate(role, proof)`` must independently verify an opaque proof and
    return verified claims. It is a trust boundary, not implemented here. Inputs
    must be freshly read by a future caller; this function performs no I/O or
    reservation and intentionally returns HOLD even after successful validation.
    """
    expected = {'version','source','draft_sha256','payload_sha256','media','editorial','policy_sha256','decisions'}
    if not isinstance(contract,dict) or set(contract) != expected or contract['version'] != VERSION:
        raise ValueError('invalid contract schema')
    if not isinstance(policy,dict) or set(policy) != {'version','owner_id','content_specialists','rights_specialists','scope'}:
        raise ValueError('invalid policy')
    if (not isinstance(trusted_policy_sha256,str) or digest_object(policy) != trusted_policy_sha256
            or contract['policy_sha256'] != trusted_policy_sha256):
        raise ValueError('policy binding mismatch')
    if (not isinstance(policy['version'],str) or not policy['version'].strip() or not isinstance(policy['owner_id'],str) or not policy['owner_id'].strip()
            or not isinstance(policy['scope'],str) or not policy['scope'].strip()
            or any(not isinstance(policy[k],list) or not policy[k] or any(not isinstance(x,str) or not x.strip() for x in policy[k]) for k in ('content_specialists','rights_specialists'))):
        raise ValueError('incomplete policy')
    source = contract['source']
    if not isinstance(source,dict) or set(source) != {'id','url','label','sha256'}:
        raise ValueError('invalid source binding')
    match = [item for item in SOURCES if item['id'] == source['id']]
    if (len(match) != 1 or source['url'] != match[0]['url'] or source['label'] != LABELS[source['id']]
            or source['sha256'] != digest_bytes(source_bytes)):
        raise ValueError('source mismatch')
    if contract['draft_sha256'] != digest_bytes(draft_bytes) or contract['payload_sha256'] != digest_bytes(payload_bytes):
        raise ValueError('draft or payload changed')
    media = contract['media']
    if not isinstance(media,list) or not 1 <= len(media) <= 10 or not isinstance(media_bytes,dict) or not isinstance(rights_evidence_bytes,dict):
        raise ValueError('media evidence required')
    ids = set()
    for item in media:
        if not isinstance(item,dict) or set(item) != {'id','sha256','provenance_url','credit','rights_basis','rights_evidence_sha256','alt'}:
            raise ValueError('invalid media schema')
        key = item['id']
        if not isinstance(key,str) or not key or key in ids:
            raise ValueError('invalid media identity')
        ids.add(key); _https(item['provenance_url'])
        if (not isinstance(item['credit'],str) or not item['credit'].strip() or not isinstance(item['alt'],str) or not item['alt'].strip() or not isinstance(item['rights_basis'],str) or not item['rights_basis'].strip()
                or key not in media_bytes or key not in rights_evidence_bytes
                or item['sha256'] != digest_bytes(media_bytes[key]) or item['rights_evidence_sha256'] != digest_bytes(rights_evidence_bytes[key])):
            raise ValueError('media or rights evidence changed')
    if set(media_bytes) != ids or set(rights_evidence_bytes) != ids:
        raise ValueError('unreviewed media or rights evidence')
    draft = strict_json(draft_bytes.decode('utf-8'))
    draft_source = draft['source']['metadata']
    if (draft_source['source_id'] != source['id'] or draft_source['source_url'] != source['url']
            or draft_source['source_sha256'] != source['sha256'] or draft['source']['source_label'] != source['label']
            or build_payload(draft_bytes, media, contract['editorial']) != payload_bytes):
        raise ValueError('payload is not canonical for reviewed draft and media')
    bound = {k:contract[k] for k in ('version','source','draft_sha256','payload_sha256','media','editorial','policy_sha256')}
    binding_sha = digest_object(bound)
    decisions = contract['decisions']
    if not isinstance(decisions,dict) or set(decisions) != {'owner','content_specialist','rights_specialist'}:
        raise ValueError('missing decisions')
    if not callable(authenticate): raise ValueError('authenticator missing')
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None: raise ValueError('timezone required')
    for role in ('owner','content_specialist','rights_specialist'):
        proof = decisions[role]
        if not isinstance(proof,str) or not proof: raise ValueError('missing decision proof')
        claim = authenticate(role, proof)
        if not isinstance(claim,dict) or set(claim) != {'subject','role','binding_sha256','verdict','expires_at','scope'}:
            raise ValueError('invalid authenticated claim')
        allowed = [policy['owner_id']] if role == 'owner' else policy[role+'s']
        try: expiry = datetime.fromisoformat(claim['expires_at'].replace('Z','+00:00'))
        except (AttributeError,ValueError): raise ValueError('invalid decision expiry') from None
        if (claim['subject'] not in allowed or claim['role'] != role or claim['binding_sha256'] != binding_sha
                or claim['verdict'] != 'approve' or claim['scope'] != policy['scope']
                or expiry.tzinfo is None or expiry <= current):
            raise ValueError('decision not valid for this binding')
    return {'status':'HOLD', 'binding_sha256':binding_sha, 'reason':'publication integration and trusted authenticators unavailable'}
