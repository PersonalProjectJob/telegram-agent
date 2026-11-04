"""Official-media upload using Scratch's chooser, not publication permission."""
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from .official_native_guard import _live_inputs
from .localization import strict_json
from .ui_image_upload import upload_with_chooser, scratch_upload_payload
from .official_upload_controller import _sync_directory_chain
from .official_upload_evidence import verify_request, bind


def upload(root,evidence_path,record_sha,page,dialog,media_id,role):
    """Revalidate evidence and upload one image; caller must hold worker lock.

    A response is an upload receipt only: it proves neither CDN visual fidelity
    nor article Create/readback, and must never be used as publication approval.
    """
    if role not in ('cover','gallery'): raise ValueError('invalid image role')
    payload,boundary,mapping,originals,policy=_live_inputs(root,evidence_path,record_sha)
    if boundary.get('status')!='REVALIDATED_NOT_PUBLISHABLE' or media_id not in originals:
        raise ValueError('verified media unavailable')
    canonical=strict_json(payload.decode('utf-8')); media=canonical.get('media',[])
    matches=[item for item in media if item.get('id')==media_id]
    if len(matches)!=1 or hashlib.sha256(originals[media_id]).hexdigest()!=matches[0].get('sha256'):
        raise ValueError('media binding mismatch')
    raw=originals[media_id]
    if raw.startswith(b'\xff\xd8\xff'): name,mime='official.jpg','image/jpeg'
    elif raw.startswith(b'\x89PNG\r\n\x1a\n'): name,mime='official.png','image/png'
    else: raise ValueError('unsupported image')
    # Record before the network action. A crash or uncertain response requires
    # explicit reconciliation, never a silent duplicate upload.
    folder=Path(evidence_path); root=Path(root).resolve(); directory=folder/'upload-intents'
    if directory.is_symlink(): raise ValueError('unsafe upload intent directory')
    directory.mkdir(mode=0o700,exist_ok=True)
    key=hashlib.sha256((record_sha+'\x00'+media_id+'\x00'+role).encode()).hexdigest()
    legacy_key=hashlib.sha256((record_sha+'\x00'+media_id).encode()).hexdigest()
    if (directory/(legacy_key+'.json')).exists() or (directory/(legacy_key+'.json')).is_symlink():
        raise ValueError('prior upload requires reconciliation')
    intent=directory/(key+'.json')
    data=(json.dumps({'record_sha256':record_sha,'media_id':media_id,'role':role,'source_sha256':matches[0]['sha256']},sort_keys=True)+'\n').encode()
    fd=os.open(intent,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'wb') as stream: stream.write(data);stream.flush();os.fsync(stream.fileno())
    _sync_directory_chain(directory,root)
    button='Upload Cover Image' if role=='cover' else 'Upload Images'
    def matches_response(r):
        try:
            parsed=urlsplit(r.url); request=r.request
            if (request.method!='POST' or parsed.scheme!='https' or parsed.hostname!='api.nailhub.ai'
                    or parsed.port not in (None,443) or parsed.username or parsed.password
                    or parsed.path!='/api/v1/Images/quick-upload' or parsed.query or parsed.fragment): return False
            verify_request(request.post_data_buffer,request.headers.get('content-type',''),raw)
            return True
        except Exception: return False
    response=upload_with_chooser(page,dialog,button,{'name':name,'mimeType':mime,'buffer':raw},matches_response)
    # An HTTP failure belongs to this upload, not a chooser timeout. The
    # durable intent remains unresolved: even errors can follow server writes.
    if not response.ok:
        raise ValueError('upload HTTP status %s; reconciliation required' % response.status)
    declared=response.headers.get('content-length','')
    if declared and (not declared.isdecimal() or int(declared)>65536): raise ValueError('upload response size unavailable')
    response_data=response.body()
    if not isinstance(response_data,bytes) or len(response_data)>65536: raise ValueError('upload response too large')
    # Retain the bounded response privately even if its schema or binding fails.
    # It is evidence for reconciliation, not authorization to publish.
    raw_dir=folder/'direct-upload-responses'
    if raw_dir.is_symlink(): raise ValueError('unsafe response directory')
    raw_dir.mkdir(mode=0o700,exist_ok=True)
    raw_path=raw_dir/(key+'.json')
    fd=os.open(raw_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'wb') as stream: stream.write(response_data);stream.flush();os.fsync(stream.fileno())
    _sync_directory_chain(raw_dir,root)
    parsed=strict_json(response_data.decode('utf-8'));url=scratch_upload_payload(parsed)
    binding=bind(response.request.post_data_buffer,response.request.headers.get('content-type',''),raw,parsed)
    receipt_dir=folder/'direct-upload-receipts'
    if receipt_dir.is_symlink():raise ValueError('unsafe receipt directory')
    receipt_dir.mkdir(mode=0o700,exist_ok=True)
    receipt=receipt_dir/(key+'.json')
    fd=os.open(receipt,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(binding);stream.flush();os.fsync(stream.fileno())
    _sync_directory_chain(receipt_dir,root)
    after=_live_inputs(root,evidence_path,record_sha)
    if (after[0],after[2],after[3],after[4])!=(payload,mapping,originals,policy):
        raise ValueError('verified inputs changed during upload')
    return {'status':'UPLOADED_NOT_PUBLISHABLE','media_id':media_id,'source_sha256':matches[0]['sha256'],
            'url':url,'role':role,'binding':strict_json(binding.decode('utf-8'))}
