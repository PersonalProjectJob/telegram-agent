"""Prepare official publication evidence without enabling reservation/Create.

All factual, freshness and media checks remain in the trusted capture boundary.
This entry point deliberately does not accept injected verifiers or network clients.
"""
from .localization import strict_json
from .official_payload import build
from .official_sources import SOURCES
from .official_auto_evidence import capture_live
from .official_tax import source_is_tax


def prepare(root, source_id, source_sha, draft_bytes, editorial, media, images, rights_evidence):
    matches=[item for item in SOURCES if item['id']==source_id]
    if len(matches)!=1: raise ValueError('official source not allowed')
    if (not isinstance(source_sha,str) or len(source_sha)!=64
            or any(c not in '0123456789abcdef' for c in source_sha)):
        raise ValueError('invalid source digest')
    if not isinstance(draft_bytes,bytes): raise ValueError('draft bytes required')
    draft=strict_json(draft_bytes.decode('utf-8'))
    try: metadata=draft['source']['metadata']
    except (KeyError,TypeError): raise ValueError('draft source missing') from None
    if (not isinstance(metadata,dict) or metadata.get('source_id')!=source_id
            or metadata.get('source_sha256')!=source_sha):
        raise ValueError('draft source identity mismatch')
    try: reference=draft['source']['reference_text']
    except (KeyError,TypeError): raise ValueError('draft reference missing') from None
    payload=build(draft_bytes,media,editorial,allow_official_tax=source_is_tax(matches[0],metadata.get('title',''),reference))
    result=capture_live(root,source_id,source_sha,draft_bytes,payload,editorial,media,images,rights_evidence)
    if (not isinstance(result,dict) or result.get('status')!='ELIGIBLE_FOR_NATIVE_VERIFICATION_ONLY'
            or result.get('publication_enabled') is not False):
        raise ValueError('trusted evidence capture incomplete')
    return result
