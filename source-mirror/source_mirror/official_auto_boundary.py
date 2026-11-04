"""Revalidation prototype for reservation/Create. Not wired to publisher."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from .localization import digest, model_response, strict_json, validate_review, packed
from .official_auto_eligibility import assess,SourceChanged,verify_factual_envelope
from .official_auto_eligibility import load_media_policy, load_runtime_policy, trusted_broker_call
from .agy_broker import MODEL, EFFORT
from .official_sources import SOURCES, fetch


def validate_at_boundary(root, evidence_path, expected_record_sha, config, *, evaluator=assess,
                         post_refetch=fetch, policy_loader=load_media_policy, **options):
    """Re-read exact evidence then re-run fresh source, media, policy and MQM checks."""
    root=Path(root).resolve(); folder=Path(evidence_path)
    if (not folder.is_absolute() or folder!=folder.resolve() or folder.is_symlink() or not folder.is_dir()
            or len(expected_record_sha)!=64 or folder.name!=expected_record_sha):
        raise ValueError('invalid evidence location')
    try: relative=folder.relative_to(root/'official-auto-evidence')
    except ValueError: raise ValueError('evidence outside state root') from None
    if len(relative.parts)!=3 or any((root/'official-auto-evidence'/Path(*relative.parts[:i])).is_symlink() for i in range(0,4)):
        raise ValueError('unsafe evidence path')
    record_path=folder/'record.json'
    if record_path.is_symlink(): raise ValueError('symlinked evidence')
    record_bytes=record_path.read_bytes()
    if hashlib.sha256(record_bytes).hexdigest()!=expected_record_sha: raise ValueError('evidence record changed')
    record=strict_json(record_bytes.decode('utf-8'))
    if (not isinstance(record,dict) or record.get('status')!='ELIGIBLE_FOR_NATIVE_VERIFICATION_ONLY'
            or record.get('publication_enabled') is not False or not isinstance(record.get('media_manifest'),list)):
        raise ValueError('invalid evidence status')
    media=record['media_manifest']; manifest=record.get('files')
    expected_names={'refetched-source.html','draft.json','payload.json'} | {f'media/{i:02d}' for i in range(1,len(media)+1)} | {f'rights/{i:02d}.txt' for i in range(1,len(media)+1)}
    if not isinstance(manifest,dict) or set(manifest)!=expected_names or not 1<=len(media)<=10: raise ValueError('incomplete evidence files')
    data={}
    for name in expected_names:
        path=folder/name
        if path.is_symlink() or path.parent.is_symlink() or not path.is_file(): raise ValueError('invalid evidence file')
        data[name]=path.read_bytes()
        if hashlib.sha256(data[name]).hexdigest()!=manifest[name]: raise ValueError('evidence file changed')
    if (hashlib.sha256(data['refetched-source.html']).hexdigest()!=record.get('source_sha256')
            or hashlib.sha256(data['draft.json']).hexdigest()!=record.get('draft_sha256')
            or hashlib.sha256(data['payload.json']).hexdigest()!=record.get('payload_sha256')
            or digest(record.get('media_policy'))!=record.get('media_policy_sha256')
            or digest(record.get('factual_envelope'))!=record.get('factual_output_sha256')):
        raise ValueError('evidence binding changed')
    if isinstance(record['factual_envelope'],dict) and set(record['factual_envelope'])!={'status','response'}:
        verify_factual_envelope(record['factual_envelope'])
    review=model_response(record['factual_envelope']); validate_review(review)
    if review!=record.get('factual_review'): raise ValueError('factual evidence changed')
    images={item['id']:data[f'media/{i:02d}'] for i,item in enumerate(media,1)}
    rights={item['id']:data[f'rights/{i:02d}.txt'] for i,item in enumerate(media,1)}
    if relative.parts[0]=='sba-business-plan':
        from .official_draft import source_snapshot
        from .official_tax import source_is_tax
        from . import official_claim_alignment
        snapshot=source_snapshot(root,relative.parts[0],relative.parts[1])
        item=next(item for item in SOURCES if item['id']==relative.parts[0])
        if source_is_tax(item,snapshot['metadata']['title'],snapshot['reference_text']):
            alignment=record.get('claim_alignment')
            if not isinstance(alignment,dict) or not isinstance(alignment.get('envelope'),dict): raise ValueError('claim alignment evidence missing')
            checked=official_claim_alignment.validate(snapshot,strict_json(data['draft.json'].decode('utf-8')),
                                                      record['editorial'],media,rights,record['media_policy'],alignment['envelope'])
            checked['envelope']=alignment['envelope']
            if packed(checked)!=packed(alignment): raise ValueError('claim alignment evidence changed')
    fresh=evaluator(root,relative.parts[0],relative.parts[1],data['draft.json'],data['payload.json'],
                    record['editorial'],media,images,rights,config,**options)
    for key in ('source_sha256','source_url','source_scope','draft_sha256','payload_sha256','media_policy_sha256'):
        if fresh.get(key)!=record.get(key): raise ValueError('fresh boundary evidence differs')
    if fresh.get('status')!='ELIGIBLE_FOR_NATIVE_VERIFICATION_ONLY' or fresh.get('publication_enabled') is not False:
        raise ValueError('boundary revalidation failed')
    # Re-read after the potentially slow model call; any mid-flight mutation
    # invalidates the decision. A real Create guard must still run at POST.
    if record_path.is_symlink() or record_path.read_bytes()!=record_bytes: raise ValueError('record changed during evaluation')
    for name,original in data.items():
        path=folder/name
        if path.is_symlink() or path.parent.is_symlink() or path.read_bytes()!=original:
            raise ValueError('evidence changed during evaluation')
    policy=policy_loader()
    if digest(policy)!=record['media_policy_sha256']: raise ValueError('policy changed during evaluation')
    current=datetime.now(timezone.utc)
    for item in media:
        matches=[entry for entry in policy['entries'] if isinstance(entry,dict) and entry.get('source_id')==relative.parts[0] and entry.get('image_sha256')==item['sha256']]
        if len(matches)!=1: raise ValueError('media policy changed')
        try: expiry=datetime.fromisoformat(matches[0]['valid_until_utc'].replace('Z','+00:00'))
        except (KeyError,AttributeError,ValueError): raise ValueError('media policy expiry invalid') from None
        if expiry.tzinfo is None or expiry<=current: raise ValueError('media policy expired')
    item=next((item for item in SOURCES if item['id']==relative.parts[0]),None)
    if item is None: raise ValueError('source unavailable')
    after=post_refetch(item)
    if not isinstance(after,bytes):raise ValueError('source unavailable after evaluation')
    if hashlib.sha256(after).hexdigest()!=record['source_sha256']:
        raise SourceChanged('source changed during evaluation')
    return {'status':'REVALIDATED_NOT_PUBLISHABLE','record_sha256':expected_record_sha,
            'payload_sha256':record['payload_sha256'],'category_mapping_sha256':digest(policy['category_mapping']),
            'checked_at_utc':datetime.now(timezone.utc).isoformat()}


def validate_live_boundary(root, evidence_path, expected_record_sha):
    """Production-shaped wrapper has no caller-controlled verifier or clock.

    It still does not authorize publication and is not wired into the publisher.
    """
    policy=load_runtime_policy()
    config={'localization':{'enabled':True,'model':MODEL,'effort':EFFORT,'socket_path':policy['socket_path']}}
    def trusted_evaluator(*args,**kwargs):
        options={}
        if policy.get('version')=='official-runtime-policy-v2':
            options['factual_inference']=lambda prompt:trusted_broker_call(policy,prompt,structured_kind='mqm-review')
        result=assess(*args,**kwargs,inference=lambda prompt:trusted_broker_call(policy,prompt),**options)
        if load_runtime_policy()!=policy: raise ValueError('runtime policy changed during review')
        return result
    return validate_at_boundary(root,evidence_path,expected_record_sha,config,evaluator=trusted_evaluator)
