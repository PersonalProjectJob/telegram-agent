"""Preview transformed CDN bytes using a separately sandboxed comparator.

This does not prove the CDN transformation, media rights, or authorize Create.
"""
from datetime import datetime,timezone
import hashlib
import os
from pathlib import Path
import stat
from urllib.parse import urlsplit
from .localization import digest,strict_json
from .official_native_guard import _live_inputs,readback
from .isolated_compare_client import compare
from .official_upload_controller import UploadObserver

POLICY_PATH='/etc/nailhub-source-mirror/official-image-comparison-policy.json'


def load_policy():
    path=Path(POLICY_PATH)
    if os.name!='posix' or not path.is_absolute(): raise ValueError('comparison policy unavailable')
    for component in (path,*path.parents):
        info=component.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid!=0 or info.st_mode & 0o022: raise ValueError('untrusted comparison policy')
    if not path.is_file() or path.stat().st_size>1_000_000: raise ValueError('invalid comparison policy')
    value=strict_json(path.read_text(encoding='utf-8'))
    if not isinstance(value,dict) or set(value)!={'version','entries'} or value['version']!='official-image-comparison-policy-v1' or not isinstance(value['entries'],list):
        raise ValueError('invalid comparison policy schema')
    return value


def inspect(root,evidence_path,record_sha,observer):
    """Revalidate source, compare exact observed output, then reread its bytes."""
    payload,boundary,mapping,originals,media_policy=_live_inputs(root,evidence_path,record_sha)
    value=strict_json(payload.decode('utf-8'))
    if not isinstance(observer,UploadObserver) or not observer.closed or not isinstance(observer.observation,dict):
        raise ValueError('observed upload unavailable')
    proof=observer.observation
    if proof.get('record_sha256')!=record_sha or proof.get('status')!='CANARY_ONLY_NOT_PUBLISHABLE': raise ValueError('upload observation mismatch')
    media_id=proof.get('media_id'); matches=[item for item in value['media'] if item['id']==media_id]
    if len(matches)!=1 or media_id not in originals: raise ValueError('observed media missing')
    descriptor=matches[0];binding=proof.get('binding')
    if not isinstance(binding,dict) or binding.get('source_sha256')!=descriptor['sha256'] or hashlib.sha256(originals[media_id]).hexdigest()!=descriptor['sha256']:
        raise ValueError('observed source media changed')
    policy=load_policy(); entries=[entry for entry in policy['entries'] if isinstance(entry,dict)
        and entry.get('source_id')==value['source']['id'] and entry.get('source_sha256')==Path(evidence_path).parent.name
        and entry.get('image_sha256')==descriptor['sha256']]
    if len(entries)!=1: raise ValueError('reviewed comparison policy unavailable')
    rule=entries[0]
    if set(rule)!={'source_id','source_sha256','image_sha256','mode','max_rms','max_channel_delta','max_aspect_error','min_scale','resample','valid_until_utc'}:
        raise ValueError('invalid comparison policy entry')
    try: expiry=datetime.fromisoformat(rule['valid_until_utc'].replace('Z','+00:00'))
    except (AttributeError,ValueError): raise ValueError('invalid comparison policy expiry') from None
    if expiry.tzinfo is None or expiry<=datetime.now(timezone.utc): raise ValueError('comparison policy expired')
    receipt=binding.get('response')
    if not isinstance(receipt,dict): raise ValueError('upload receipt missing')
    url=receipt.get('imageUrl'); output=readback(url)
    threshold={key:rule[key] for key in ('mode','max_rms','max_channel_delta','max_aspect_error','min_scale','resample')}
    review=compare(originals[media_id],output,threshold)
    if review.get('status')!='VISUAL_QA_NOT_PUBLISHABLE': raise ValueError('visual comparison unavailable')
    thumb=receipt.get('thumbnailUrl'); thumb_path=receipt.get('thumbnailRelativePath')
    if not isinstance(thumb,str) or not isinstance(thumb_path,str): raise ValueError('invalid thumbnail receipt')
    if bool(thumb)!=bool(thumb_path): raise ValueError('incomplete thumbnail receipt')
    thumbnail=None
    if thumb:
        parsed=urlsplit(thumb)
        if (parsed.scheme!='https' or parsed.hostname!='vlink-storages-prod.sgp1.cdn.digitaloceanspaces.com'
                or parsed.username or parsed.password or parsed.port not in (None,443) or parsed.query or parsed.fragment
                or parsed.path.lstrip('/')!=thumb_path.lstrip('/')): raise ValueError('invalid thumbnail URL')
        thumbnail_bytes=readback(thumb);thumbnail_review=compare(originals[media_id],thumbnail_bytes,threshold)
        thumbnail={'url':thumb,'sha256':hashlib.sha256(thumbnail_bytes).hexdigest(),'comparison':thumbnail_review}
    after=_live_inputs(root,evidence_path,record_sha)
    if (after[0],after[2],after[3],after[4])!=(payload,mapping,originals,media_policy) or digest(load_policy())!=digest(policy):
        raise ValueError('trusted evidence or comparison policy changed')
    if hashlib.sha256(readback(url)).hexdigest()!=hashlib.sha256(output).hexdigest(): raise ValueError('CDN output changed during review')
    if thumbnail is not None and hashlib.sha256(readback(thumb)).hexdigest()!=thumbnail['sha256']: raise ValueError('CDN thumbnail changed during review')
    return {'status':'VISUAL_QA_NOT_PUBLISHABLE','record_sha256':record_sha,'media_id':media_id,
            'source_sha256':descriptor['sha256'],'output_sha256':hashlib.sha256(output).hexdigest(),
            'comparison_policy_sha256':digest(policy),'comparison':review,'thumbnail':thumbnail,'publication_enabled':False}
