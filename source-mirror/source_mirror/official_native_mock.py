"""Sandbox-only Create mapping. Keys were observed in 2026-10-28 evidence.

Image-object keys were observed; mapping and byte readback remain unverified.
Never call the real UI or API with this structure; official publisher stays blocked.
"""
import json
import hashlib
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit
from .localization import digest


def mock_request(payload_bytes, cover, images, category_mapping):
    payload=json.loads(payload_bytes)
    if not isinstance(images,list) or len(images)!=len(payload['media']) or not images:
        raise ValueError('invalid media URL mapping')
    keys={'id','imageUrl','imageRelativePath','thumbnailUrl','thumbnailRelativePath'}
    for item in [cover,*images]:
        if (not isinstance(item,dict) or set(item)!=keys or any(not isinstance(v,str) for v in item.values())
                or not item['id'] or not item['imageUrl'] or not item['imageRelativePath']
                or bool(item['thumbnailUrl'])!=bool(item['thumbnailRelativePath'])): raise ValueError('invalid media object')
        for url in ([item['imageUrl'],item['thumbnailUrl']] if item['thumbnailUrl'] else [item['imageUrl']]):
            parsed=urlsplit(url)
            if (parsed.scheme!='https' or parsed.hostname!='vlink-storages-prod.sgp1.cdn.digitaloceanspaces.com'
                    or parsed.username or parsed.password or parsed.fragment): raise ValueError('invalid media URL')
    if len({item['id'] for item in images})!=len(images): raise ValueError('duplicate image ID')
    translations=[]
    for code,key in [('VI','vi'),('EN','en')]:
        locale=payload['locales'][key]
        translations.append({'languageCode':code,'title':locale['title'],'summary':locale['summary'],
                             'metaTitle':locale['meta_title'],'metaDescription':locale['meta_description'],'content':locale['body_html']})
    if not isinstance(category_mapping,dict) or payload['category'] not in category_mapping:
        raise ValueError('mock category mapping unavailable')
    category_id=category_mapping[payload['category']]
    if not isinstance(category_id,str) or len(category_id)!=36:
        raise ValueError('mock category identifier required')
    try:
        if str(uuid.UUID(category_id))!=category_id.lower(): raise ValueError()
    except ValueError: raise ValueError('invalid mock category identifier') from None
    when=datetime.fromisoformat(payload['published_at'].replace('Z','+00:00'))
    if when.tzinfo is None: raise ValueError('mock timestamp requires timezone')
    return {'translations':translations,'externalSourceUrl':payload['source']['url'],
            'hashtags':payload['hashtags'],'categoryId':category_id,'status':payload['status'],
            'isPublic':payload['is_public'],'publishedAt':when.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z'),
            'isFeatured':payload['is_featured'],'videoUrls':payload['video_urls'], 'coverImage':cover,'images':images}


def mock_guard(expected, actual):
    """Exact mock equality only. This function performs no network operation."""
    if (not isinstance(expected,dict) or not isinstance(actual,dict)
            or json.dumps(actual,sort_keys=True,separators=(',',':'),allow_nan=False)!=json.dumps(expected,sort_keys=True,separators=(',',':'),allow_nan=False)):
        raise ValueError('mock native payload changed')
    return 'MOCK_ONLY'


def mock_route(payload_bytes, cover, images, category_mapping, remote_assets, actual, boundary):
    """Exercise a fail-closed route in memory, never a real Playwright route."""
    if not callable(boundary): raise ValueError('boundary unavailable')
    result=boundary()
    if (result.get('status')!='REVALIDATED_NOT_PUBLISHABLE'
            or result.get('payload_sha256')!=hashlib.sha256(payload_bytes).hexdigest()
            or result.get('category_mapping_sha256')!=digest(category_mapping)):
        raise ValueError('boundary not revalidated')
    payload=json.loads(payload_bytes)
    objects=[cover,*images]; source_media=[payload['media'][0],*payload['media']]
    expected_urls={item[key] for item in objects for key in ('imageUrl','thumbnailUrl') if item[key]}
    if not isinstance(remote_assets,dict) or set(remote_assets)!=expected_urls: raise ValueError('media readback unavailable')
    for expected_media,item in zip(source_media,objects):
        for key in ('imageUrl','thumbnailUrl'):
            if not item[key]: continue
            data=remote_assets[item[key]]
            if not isinstance(data,bytes) or hashlib.sha256(data).hexdigest()!=expected_media['sha256']:
                raise ValueError('media readback differs')
    expected=mock_request(payload_bytes,cover,images,category_mapping)
    return mock_guard(expected,actual)
