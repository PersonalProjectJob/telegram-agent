"""Persist bounded evidence for a proposed official automatic publication.

Records are diagnostic and do not grant publication rights.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from .official_auto_eligibility import assess
from .localization import packed


def capture(root, source_id, source_sha, draft_bytes, payload_bytes, editorial, media, images, rights_evidence, config, **check_options):
    result=assess(root,source_id,source_sha,draft_bytes,payload_bytes,editorial,media,images,rights_evidence,config,**check_options)
    source=base64.b64decode(result['refetched_source_base64'],validate=True)
    record={k:v for k,v in result.items() if k!='refetched_source_base64'}
    record['editorial']=editorial; record['media_manifest']=media
    files={'refetched-source.html':source,'draft.json':draft_bytes,'payload.json':payload_bytes}
    for index,item in enumerate(media,1):
        files[f'media/{index:02d}']=images[item['id']]
        files[f'rights/{index:02d}.txt']=rights_evidence[item['id']]
    record['files']={name:hashlib.sha256(value).hexdigest() for name,value in files.items()}
    record_bytes=(packed(record)+'\n').encode('utf-8')
    identity=hashlib.sha256(record_bytes).hexdigest()
    root=Path(root).resolve(); base=root/'official-auto-evidence'; parent=base/source_id/source_sha
    for directory in (base,base/source_id,parent):
        if directory.is_symlink(): raise ValueError('unsafe evidence directory')
        directory.mkdir(exist_ok=True,mode=0o700)
    target=parent/identity
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_dir() or (target/'record.json').read_bytes()!=record_bytes:
            raise ValueError('existing evidence differs')
        for name,data in files.items():
            path=target/name
            if path.is_symlink() or path.parent.is_symlink() or path.read_bytes()!=data:
                raise ValueError('existing evidence corrupted')
        return {'status':result['status'],'path':str(target),'record_sha256':identity,'publication_enabled':False}
    temp=Path(tempfile.mkdtemp(prefix='.evidence-',dir=parent))
    try:
        for name,data in {**files,'record.json':record_bytes}.items():
            path=temp/name; path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as stream:
                stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.rename(temp,target)
    finally:
        if temp.exists(): shutil.rmtree(temp)
    return {'status':result['status'],'path':str(target),'record_sha256':identity,'publication_enabled':False}


def capture_live(root, source_id, source_sha, draft_bytes, payload_bytes, editorial, media, images, rights_evidence):
    """Production-shaped evidence capture: no caller-selected broker or verifier."""
    from .official_auto_eligibility import load_runtime_policy, trusted_broker_call
    from .agy_broker import MODEL, EFFORT
    policy=load_runtime_policy()
    config={'localization':{'enabled':True,'model':MODEL,'effort':EFFORT,'socket_path':policy['socket_path']}}
    def inference(prompt):
        value=trusted_broker_call(policy,prompt)
        if load_runtime_policy()!=policy: raise ValueError('runtime policy changed during review')
        return value
    options={}
    if policy.get('version')=='official-runtime-policy-v2':
        def factual(prompt):
            value=trusted_broker_call(policy,prompt,structured_kind='mqm-review')
            if load_runtime_policy()!=policy:raise ValueError('runtime policy changed during review')
            return value
        options['factual_inference']=factual
    return capture(root,source_id,source_sha,draft_bytes,payload_bytes,editorial,media,images,rights_evidence,config,inference=inference,**options)
