"""Prototype native request/readback checker; it does not send a request.

This checker is not wired to the live publisher. Legacy ``verify`` requires
original-byte equality. The output-binding prototype separately binds reviewed
originals to receipt URLs and captured CDN output bytes, allowing re-encoding.
Neither a caller-provided receipt nor a hash proves the output was transformed
from that original by a native upload. Every successful result remains expressly
NOT_PUBLISHABLE and retains that unresolved provenance blocker.
"""
import hashlib
import http.client
import ipaddress
import json
import socket
import ssl
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from .localization import digest, strict_json, packed
from .official_native_mock import mock_request
from .official_generated_media import is_generated

CDN='vlink-storages-prod.sgp1.cdn.digitaloceanspaces.com'
MAX_BYTES=20*1024*1024
OUTPUT_VERSION='official-native-output-binding-v1'


def readback(url):
    parsed=urlsplit(url)
    if (parsed.scheme!='https' or parsed.hostname!=CDN or parsed.username or parsed.password
            or parsed.port not in (None,443) or parsed.query or parsed.fragment): raise ValueError('invalid CDN URL')
    addresses=socket.getaddrinfo(CDN,443,type=socket.SOCK_STREAM)
    if not addresses: raise ValueError('CDN address unavailable')
    for row in addresses:
        if not ipaddress.ip_address(row[4][0]).is_global: raise ValueError('non-public CDN address')
    # Connect to the checked IP, while retaining the CDN hostname for TLS SNI
    # and certificate verification; do not resolve again or use proxy env vars.
    target=(addresses[0][4][0],443)
    class PinnedHTTPS(http.client.HTTPSConnection):
        def connect(self):
            raw=socket.create_connection(target,timeout=self.timeout)
            try: self.sock=ssl.create_default_context().wrap_socket(raw,server_hostname=CDN)
            except BaseException: raw.close(); raise
    connection=PinnedHTTPS(CDN,timeout=12)
    try:
        connection.request('GET',parsed.path or '/',headers={'Accept':'image/*'})
        response=connection.getresponse()
        if response.status!=200: raise ValueError('unexpected CDN response')
        data=response.read(MAX_BYTES+1)
    finally:
        connection.close()
    if not data or len(data)>MAX_BYTES: raise ValueError('invalid CDN content')
    return data


def verify(payload_bytes, boundary, category_mapping, cover_receipt, gallery_receipts, actual_request, *, fetch=readback):
    """Compare exact native payload and verify every uploaded image's bytes.

    The caller must re-run authenticated boundary verification at POST. This
    standalone function cannot authorize publication or prove upload provenance.
    """
    if (not isinstance(boundary,dict) or boundary.get('status')!='REVALIDATED_NOT_PUBLISHABLE'
            or boundary.get('payload_sha256')!=hashlib.sha256(payload_bytes).hexdigest()
            or boundary.get('category_mapping_sha256')!=digest(category_mapping)):
        raise ValueError('boundary binding unavailable')
    expected=mock_request(payload_bytes,cover_receipt,gallery_receipts,category_mapping)
    if not isinstance(actual_request,bytes): raise ValueError('raw request bytes required')
    actual=strict_json(actual_request.decode('utf-8'))
    if not isinstance(actual,dict) or packed(actual)!=packed(expected):
        raise ValueError('native request differs from checked payload or upload receipts')
    payload=json.loads(payload_bytes)
    objects=[cover_receipt,*gallery_receipts]; media=payload['media']
    seen={}
    for descriptor,receipt in zip(media,objects):
        for url_key,path_key in (('imageUrl','imageRelativePath'),('thumbnailUrl','thumbnailRelativePath')):
            url=receipt[url_key]
            if not url and not receipt[path_key]: continue
            parsed=urlsplit(url)
            if (parsed.scheme!='https' or parsed.hostname!=CDN or parsed.username or parsed.password
                    or parsed.port not in (None,443) or parsed.query or parsed.fragment
                    or parsed.path.lstrip('/')!=receipt[path_key].lstrip('/')):
                raise ValueError('upload receipt URL or path mismatch')
            expected_hash=descriptor['sha256']
            if url in seen and seen[url]!=expected_hash: raise ValueError('CDN URL reused for different media')
            seen[url]=expected_hash
    for url,expected_hash in seen.items():
        data=fetch(url)
        if not isinstance(data,bytes) or not data or len(data)>MAX_BYTES or hashlib.sha256(data).hexdigest()!=expected_hash:
            raise ValueError('CDN content is not byte-identical to reviewed media')
    return {'status':'NATIVE_SHAPE_VERIFIED_NOT_PUBLISHABLE','payload_sha256':hashlib.sha256(payload_bytes).hexdigest()}


def _receipt_objects(payload, cover, gallery):
    if not isinstance(payload.get('media'),list) or not payload['media'] or not isinstance(gallery,list) or len(gallery)!=len(payload['media'])-1:
        raise ValueError('invalid output receipt count')
    objects=[cover,*gallery]
    for receipt in objects:
        if not isinstance(receipt,dict) or set(receipt)!={'id','imageUrl','imageRelativePath','thumbnailUrl','thumbnailRelativePath'}:
            raise ValueError('invalid output receipt schema')
        if (any(not isinstance(value,str) for value in receipt.values()) or not receipt['id'] or not receipt['imageUrl'] or not receipt['imageRelativePath'] or bool(receipt['thumbnailUrl'])!=bool(receipt['thumbnailRelativePath'])):
            raise ValueError('invalid output receipt value')
        for url_key,path_key in (('imageUrl','imageRelativePath'),('thumbnailUrl','thumbnailRelativePath')):
            url=receipt[url_key]; path=receipt[path_key]
            if not url: continue
            parsed=urlsplit(url)
            if (parsed.scheme!='https' or parsed.hostname!=CDN or parsed.username or parsed.password
                    or parsed.port not in (None,443) or parsed.query or parsed.fragment
                    or not parsed.path.startswith('/') or parsed.path=='/'
                    or any(segment in ('.','..') for segment in parsed.path.split('/'))
                    or '%' in parsed.path or '\\' in parsed.path
                    or path not in (parsed.path,parsed.path[1:])):
                raise ValueError('output receipt URL or path mismatch')
    return list(zip(payload['media'],objects))


def _policy_originals(payload, original_media, policy):
    if (not isinstance(policy,dict) or policy.get('version')!='official-media-policy-v1'
            or not isinstance(policy.get('entries'),list) or not isinstance(policy.get('category_mapping'),dict)):
        raise ValueError('trusted source media policy unavailable')
    media=payload['media']; source=payload['source']
    if not isinstance(original_media,dict) or set(original_media)!={item['id'] for item in media}:
        raise ValueError('original media bytes unavailable')
    current=datetime.now(timezone.utc)
    for item in media:
        data=original_media[item['id']]
        if not isinstance(data,bytes) or not data or len(data)>MAX_BYTES or hashlib.sha256(data).hexdigest()!=item['sha256']:
            raise ValueError('original media hash mismatch')
        if is_generated(item):
            if not isinstance(item.get('rights_evidence_sha256'),str) or len(item['rights_evidence_sha256'])!=64 or item.get('credit')!='AI-generated illustration':raise ValueError('generated payload missing provenance')
            continue  # Authenticated boundary revalidates bound generated evidence.
        # The full source/rights assessment is still performed by the live
        # boundary. This check prevents binding an unrelated image or policy
        # after that boundary; .gov provenance alone never grants permission.
        expected={'source_id':source['id'],'source_url':source['url'],'geographic_scope':source['scope'],
                  'image_sha256':item['sha256'],'credit':item['credit'],'jurisdiction':'WORLDWIDE'}
        matches=[entry for entry in policy['entries'] if isinstance(entry,dict) and all(entry.get(k)==v for k,v in expected.items())]
        if len(matches)!=1: raise ValueError('original media not in trusted source policy')
        try: expiry=datetime.fromisoformat(matches[0]['valid_until_utc'].replace('Z','+00:00'))
        except (KeyError,ValueError,AttributeError): raise ValueError('invalid source policy expiry') from None
        if expiry.tzinfo is None or expiry<=current: raise ValueError('source policy expired')


def _check_boundary(payload_bytes,boundary,mapping):
    if (not isinstance(boundary,dict) or boundary.get('status')!='REVALIDATED_NOT_PUBLISHABLE'
            or boundary.get('payload_sha256')!=hashlib.sha256(payload_bytes).hexdigest()
            or boundary.get('category_mapping_sha256')!=digest(mapping)):
        raise ValueError('fresh boundary binding unavailable')


def capture_outputs(payload_bytes,boundary,category_mapping,cover_receipt,gallery_receipts,
                    original_media,media_policy,*,fetch=readback):
    """Pure prototype: record CDN output identity, not transformation provenance.

    Injectable inputs support testing only. Production-shaped callers must use
    ``capture_live_outputs`` which revalidates the trusted boundary itself.
    """
    _check_boundary(payload_bytes,boundary,category_mapping)
    payload=strict_json(payload_bytes.decode('utf-8'))
    _policy_originals(payload,original_media,media_policy)
    if media_policy.get('category_mapping')!=category_mapping: raise ValueError('category policy differs')
    mock_request(payload_bytes,cover_receipt,gallery_receipts,category_mapping)
    uploads=[]; fetched={}; source_by_url={}
    for index,(descriptor,receipt) in enumerate(_receipt_objects(payload,cover_receipt,gallery_receipts)):
        outputs={}
        for url_key,path_key in (('imageUrl','imageRelativePath'),('thumbnailUrl','thumbnailRelativePath')):
            url=receipt[url_key]
            if not url: continue
            if url in source_by_url and source_by_url[url]!=descriptor['sha256']:
                raise ValueError('output URL reused across different originals')
            source_by_url[url]=descriptor['sha256']
            if url not in fetched:
                data=fetch(url)
                if not isinstance(data,bytes) or not data or len(data)>MAX_BYTES:
                    raise ValueError('invalid CDN output bytes')
                fetched[url]={'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)}
            outputs[url_key]={'url':url,'path':receipt[path_key],**fetched[url]}
        uploads.append({'role':'cover' if index==0 else 'gallery','media_id':descriptor['id'],
                        'original_sha256':descriptor['sha256'],'receipt':dict(receipt),'outputs':outputs})
    return {'version':OUTPUT_VERSION,'status':'OUTPUT_CAPTURED_NOT_PUBLISHABLE',
            'payload_sha256':hashlib.sha256(payload_bytes).hexdigest(),'category_mapping_sha256':digest(category_mapping),
            'media_policy_sha256':digest(media_policy),'boundary_record_sha256':boundary.get('record_sha256'),
            'uploads':uploads,'native_upload_provenance_verified':False,
            'publication_enabled':False,'blockers':['native_upload_request_to_output_provenance_not_proven']}


def verify_outputs(payload_bytes,boundary,category_mapping,cover_receipt,gallery_receipts,
                   original_media,media_policy,binding_bytes,expected_binding_sha,actual_request,*,fetch=readback):
    """At POST, compare frozen receipt/output binding and re-fetch exact bytes."""
    if (not isinstance(binding_bytes,bytes) or hashlib.sha256(binding_bytes).hexdigest()!=expected_binding_sha
            or not isinstance(actual_request,bytes)):
        raise ValueError('frozen output binding or native request unavailable')
    bound=strict_json(binding_bytes.decode('utf-8'))
    fresh=capture_outputs(payload_bytes,boundary,category_mapping,cover_receipt,gallery_receipts,
                          original_media,media_policy,fetch=fetch)
    if packed(bound)!=packed(fresh): raise ValueError('CDN output, receipt, source policy or boundary changed')
    actual=strict_json(actual_request.decode('utf-8'))
    expected=mock_request(payload_bytes,cover_receipt,gallery_receipts,category_mapping)
    if not isinstance(actual,dict) or packed(actual)!=packed(expected): raise ValueError('native request differs from output-bound payload')
    return {'status':'NATIVE_OUTPUT_BOUND_NOT_PUBLISHABLE','payload_sha256':fresh['payload_sha256'],
            'output_binding_sha256':expected_binding_sha,'native_upload_provenance_verified':False,
            'publication_enabled':False,'blockers':fresh['blockers']}


def _live_inputs(root,evidence_path,record_sha):
    # Import here so the live wrapper has no dependency-injection parameters.
    from .official_auto_boundary import validate_live_boundary
    from .official_auto_eligibility import load_media_policy
    boundary=validate_live_boundary(root,evidence_path,record_sha)
    folder=Path(evidence_path)
    raw=(folder/'record.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=record_sha: raise ValueError('evidence changed after boundary')
    record=strict_json(raw.decode('utf-8')); payload_bytes=(folder/'payload.json').read_bytes()
    policy=load_media_policy()
    if digest(policy)!=record.get('media_policy_sha256'): raise ValueError('trusted source policy changed')
    if hashlib.sha256(payload_bytes).hexdigest()!=record.get('payload_sha256'): raise ValueError('payload changed after boundary')
    originals={}
    for index,item in enumerate(record['media_manifest'],1):
        path=folder/'media'/f'{index:02d}'
        if path.is_symlink() or path.parent.is_symlink(): raise ValueError('unsafe original media evidence')
        data=path.read_bytes()
        if hashlib.sha256(data).hexdigest()!=record['files'][f'media/{index:02d}']: raise ValueError('original media changed after boundary')
        originals[item['id']]=data
    return payload_bytes,boundary,policy['category_mapping'],originals,policy


def capture_live_outputs(root,evidence_path,record_sha,cover_receipt,gallery_receipts):
    """Revalidate internally; never accept a caller's boundary or policy/fetch."""
    payload,boundary,mapping,originals,policy=_live_inputs(root,evidence_path,record_sha)
    result=capture_outputs(payload,boundary,mapping,cover_receipt,gallery_receipts,originals,policy)
    # Policy/source/MQM must still be current after the network readback.
    after=_live_inputs(root,evidence_path,record_sha)
    if (after[0],after[2],after[3],after[4])!=(payload,mapping,originals,policy): raise ValueError('trusted inputs changed during output capture')
    return result


def verify_live_outputs(root,evidence_path,record_sha,cover_receipt,gallery_receipts,
                        binding_bytes,expected_binding_sha,actual_request):
    """Production-shaped POST checker. A successful result still forbids Create."""
    payload,boundary,mapping,originals,policy=_live_inputs(root,evidence_path,record_sha)
    result=verify_outputs(payload,boundary,mapping,cover_receipt,gallery_receipts,originals,policy,
                          binding_bytes,expected_binding_sha,actual_request)
    after=_live_inputs(root,evidence_path,record_sha)
    if (after[0],after[2],after[3],after[4])!=(payload,mapping,originals,policy): raise ValueError('trusted inputs changed during POST output check')
    return result
