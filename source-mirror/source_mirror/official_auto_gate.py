"""Diagnostic gate for proposed official auto-publication; always HOLD for now.

Owner and specialist approval are not required by current policy. This module
does not establish media rights, fact-check editorial metadata, or enable Create.
"""
from datetime import datetime, timezone
from .official_draft import source_snapshot
from .official_payload import build
from .localization import strict_json


def assess(root, source_id, source_sha, draft_bytes, payload_bytes, editorial, media, *, now=None):
    """Check available bindings and report unavailable protections, without writes."""
    snapshot=source_snapshot(root,source_id,source_sha)
    draft=strict_json(draft_bytes.decode('utf-8'))
    if not isinstance(draft,dict) or draft.get('source')!=snapshot:
        raise ValueError('official source snapshot changed')
    if build(draft_bytes,media,editorial)!=payload_bytes:
        raise ValueError('official payload changed')
    current=now or datetime.now(timezone.utc)
    if current.tzinfo is None: raise ValueError('timezone-aware current time required')
    try: fetched=datetime.fromisoformat(snapshot['metadata']['fetched_at_utc'].replace('Z','+00:00'))
    except (KeyError,AttributeError,ValueError): raise ValueError('source capture date unavailable') from None
    if fetched.tzinfo is None: raise ValueError('source capture date lacks timezone')
    age=(current-fetched).total_seconds()
    # Conservative technical freshness bound, not a claim that source facts or
    # legal requirements are current. A fresh authoritative re-fetch is needed.
    blockers=[]
    if age<0 or age>86400: blockers.append('source_snapshot_outside_24_hour_window')
    blockers.extend(('fresh_authoritative_refetch_not_verified',
                     'editorial_factual_and_geographic_verification_not_implemented',
                     'media_rights_verification_not_implemented',
                     'native_official_publication_contract_not_verified'))
    return {'status':'HOLD','blockers':blockers,'source_id':source_id,'source_sha256':source_sha}
