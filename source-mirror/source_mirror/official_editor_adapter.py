"""Canary-only editor adapter. It cannot reserve, upload or submit a blog."""
import hashlib
from .localization import strict_json
from .official_payload import build
from .official_sources import SOURCES
from .official_tax import source_is_tax


def plan(draft_bytes, editorial, media, expected_payload):
    """Rebuild canonical payload and make form values; this is not approval."""
    if not isinstance(draft_bytes,bytes) or not isinstance(expected_payload,bytes): raise ValueError('canonical bytes required')
    draft=strict_json(draft_bytes.decode('utf-8'))
    try: source_id=draft['source']['metadata']['source_id']
    except (KeyError,TypeError): raise ValueError('source missing') from None
    sources=[item for item in SOURCES if item['id']==source_id]
    if len(sources)!=1: raise ValueError('official source not allowed')
    try: meta=draft['source']['metadata']; reference=draft['source']['reference_text']
    except (KeyError,TypeError): raise ValueError('source missing') from None
    actual=build(draft_bytes,media,editorial,allow_official_tax=source_is_tax(sources[0],meta['title'],reference))
    if actual!=expected_payload: raise ValueError('canonical payload changed')
    payload=strict_json(actual.decode('utf-8'))
    return {'status':'CANARY_ONLY_NOT_PUBLISHABLE','payload_sha256':hashlib.sha256(actual).hexdigest(),
            'source_url':payload['source']['url'],'source_label':payload['source']['label'],
            'locales':payload['locales'],'category':payload['category'],'hashtags':payload['hashtags'],
            'media':payload['media']}
