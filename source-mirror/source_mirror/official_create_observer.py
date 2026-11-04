"""Capture an aborted native Create request; never permit publication."""
from datetime import datetime,timezone
import hashlib
import os
from pathlib import Path
import uuid
from urllib.parse import urlsplit
from .localization import packed,strict_json
from .official_native_guard import _live_inputs
from .official_upload_controller import UploadObserver, _sync_directory_chain


def capture_aborted(route,root,evidence_path,record_sha,cover,gallery):
    """Abort first, then bind captured request to same-process uploads and payload."""
    request=route.request
    try: raw=request.post_data_buffer; method=request.method; url=urlsplit(request.url)
    finally: route.abort('blockedbyclient')
    return verify_observed(raw,method,url,root,evidence_path,record_sha,cover,gallery)


def verify_observed(raw,method,url,root,evidence_path,record_sha,cover,gallery):
    """Revalidate request/observations; result still cannot authorize Create."""
    if (method!='POST' or url.scheme!='https' or url.hostname!='api.nailhub.ai' or url.port not in (None,443)
            or url.username or url.password or url.path!='/api/v1/admin/blogs' or url.query or url.fragment):
        raise ValueError('unexpected create endpoint')
    if not isinstance(raw,bytes) or not raw or len(raw)>8_000_000: raise ValueError('invalid create request bytes')
    payload,boundary,mapping,originals,policy=_live_inputs(root,evidence_path,record_sha)
    source=strict_json(payload.decode('utf-8')); actual=strict_json(raw.decode('utf-8'))
    if not isinstance(actual,dict) or not isinstance(gallery,list) or len(gallery)!=len(source['media']): raise ValueError('create media mismatch')
    def observed(observer,descriptor,role,object_value):
        if not isinstance(observer,UploadObserver) or not observer.closed or not isinstance(observer.observation,dict): raise ValueError('missing upload observation')
        proof=observer.observation; binding=proof.get('binding')
        if (observer.record_sha!=record_sha or proof.get('record_sha256')!=record_sha or proof.get('role')!=role
                or proof.get('media_id')!=descriptor['id'] or not isinstance(binding,dict)
                or binding.get('source_sha256')!=descriptor['sha256']): raise ValueError('upload observation mismatch')
        receipt=binding.get('response'); keys={'imageUrl','imageRelativePath','thumbnailUrl','thumbnailRelativePath'}
        if (not isinstance(receipt,dict) or set(receipt)!=keys or not isinstance(object_value,dict)
                or set(object_value)!=keys|{'id'} or any(object_value[k]!=receipt[k] for k in keys)):
            raise ValueError('native media differs from observed response')
        ident=object_value['id']
        try:
            if not isinstance(ident,str) or str(uuid.UUID(ident))!=ident: raise ValueError()
        except (ValueError,AttributeError): raise ValueError('native media id invalid') from None
        return {'id':ident,**receipt}
    cover_value=observed(cover,source['media'][0],'cover',actual.get('coverImage'))
    objects=actual.get('images')
    if not isinstance(objects,list) or len(objects)!=len(gallery): raise ValueError('native gallery mismatch')
    images=[observed(observer,descriptor,'gallery',obj) for observer,descriptor,obj in zip(gallery,source['media'],objects)]
    if len({item['id'] for item in [cover_value,*images]})!=len(images)+1: raise ValueError('native media id reused')
    category_id=mapping.get(source['category'])
    try:
        if not isinstance(category_id,str) or str(uuid.UUID(category_id))!=category_id: raise ValueError()
    except (ValueError,AttributeError): raise ValueError('trusted category missing') from None
    when=datetime.fromisoformat(source['published_at'].replace('Z','+00:00'))
    if when.tzinfo is None: raise ValueError('publication time requires timezone')
    translations=[]
    for code,lang in (('VI','vi'),('EN','en')):
        value=source['locales'][lang]
        translations.append({'languageCode':code,'title':value['title'],'summary':value['summary'],'content':value['body_html'],
                             'metaTitle':value['meta_title'],'metaDescription':value['meta_description']})
    expected={'categoryId':category_id,'status':source['status'],'publishedAt':when.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z'),
              'isPublic':source['is_public'],'isFeatured':source['is_featured'],'coverImage':cover_value,'images':images,
              'videoUrls':source['video_urls'],'hashtags':source['hashtags'],'externalSourceUrl':source['source']['url'],'translations':translations}
    if packed(actual)!=packed(expected): raise ValueError('aborted create differs from canonical payload')
    after=_live_inputs(root,evidence_path,record_sha)
    if (after[0],after[2],after[3],after[4])!=(payload,mapping,originals,policy): raise ValueError('trusted inputs changed during canary')
    value={'version':'official-aborted-create-v1','status':'CANARY_ONLY_NOT_PUBLISHABLE','source_record_sha256':record_sha,
           'payload_sha256':hashlib.sha256(payload).hexdigest(),'request_sha256':hashlib.sha256(raw).hexdigest(),
           'cover_observation_sha256':hashlib.sha256((packed(cover.observation)+'\n').encode()).hexdigest(),
           'gallery_observation_sha256':[hashlib.sha256((packed(item.observation)+'\n').encode()).hexdigest() for item in gallery]}
    encoded=(packed(value)+'\n').encode(); sha=hashlib.sha256(encoded).hexdigest(); folder=Path(evidence_path)/'create-observations'
    if folder.is_symlink(): raise ValueError('unsafe create evidence directory')
    folder.mkdir(exist_ok=True,mode=0o700); _sync_directory_chain(folder,Path(root).resolve())
    path=folder/(sha+'.json')
    try: fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    except FileExistsError:
        if path.is_symlink() or path.read_bytes()!=encoded: raise ValueError('create observation changed')
    else:
        with os.fdopen(fd,'wb') as out: out.write(encoded); out.flush(); os.fsync(out.fileno())
        _sync_directory_chain(folder,Path(root).resolve())
    return {'status':'CANARY_ONLY_NOT_PUBLISHABLE','observation_sha256':sha,'path':str(path)}
