"""Bound a single native upload request to source bytes and its response.

This pure checker does not authenticate callers or authorize a network action;
a future trusted controller must supply bytes from the actual intercepted
request and the response belonging to that same request object.
"""
import hashlib
import json
import re
from email import policy
from email.parser import BytesParser
from urllib.parse import urlsplit
from .localization import packed

MAX_IMAGE=6_000_000
MAX_REQUEST=MAX_IMAGE+32_768
CDN='vlink-storages-prod.sgp1.cdn.digitaloceanspaces.com'


def verify_request(request_bytes,content_type,source_bytes):
    if (not isinstance(request_bytes,bytes) or len(request_bytes)>MAX_REQUEST
            or not isinstance(source_bytes,bytes) or not 0<len(source_bytes)<=MAX_IMAGE):
        raise ValueError('upload size invalid')
    if (not isinstance(content_type,str) or len(content_type)>200 or '\r' in content_type or '\n' in content_type
            or not re.fullmatch(r'multipart/form-data\s*;\s*boundary=(?:[A-Za-z0-9\x27()+_,./:=?\-]{1,70}|"[A-Za-z0-9\x27()+_,./:=?\-]{1,70}")',content_type,re.I)):
        raise ValueError('invalid upload content type')
    message=BytesParser(policy=policy.default).parsebytes(('Content-Type: '+content_type+'\r\nMIME-Version: 1.0\r\n\r\n').encode('ascii')+request_bytes)
    if message.defects or not message.is_multipart(): raise ValueError('invalid upload multipart')
    parts=list(message.iter_parts())
    if len(parts)!=1: raise ValueError('exactly one upload file required')
    part=parts[0]
    if (part.defects or part.is_multipart() or len(part.get_all('Content-Disposition',[]))!=1
            or len(part.get_all('Content-Type',[]))!=1 or part.get_all('Content-Transfer-Encoding',[])
            or set(k.lower() for k in part.keys())!={'content-disposition','content-type'}
            or part.get_content_disposition()!='form-data' or part.get_param('name',header='content-disposition')!='file'):
        raise ValueError('invalid upload part')
    params=part.get_params(header='content-disposition',unquote=True)[1:]
    if len(params)!=2 or {str(k).lower() for k,_ in params}!={'name','filename'}: raise ValueError('invalid upload part parameters')
    filename=part.get_filename()
    if (not isinstance(filename,str) or not filename or len(filename)>255
            or any(c in filename for c in ('/','\\','\x00','\r','\n'))): raise ValueError('invalid upload filename')
    media_type=part.get_content_type(); signatures={'image/jpeg':b'\xff\xd8\xff','image/png':b'\x89PNG\r\n\x1a\n'}
    if media_type not in signatures or not source_bytes.startswith(signatures[media_type]): raise ValueError('invalid upload image type')
    if part.get_payload(decode=True)!=source_bytes: raise ValueError('upload bytes changed')
    return media_type


def bind(request_bytes,content_type,source_bytes,response):
    media_type=verify_request(request_bytes,content_type,source_bytes)
    keys={'imageUrl','imageRelativePath','thumbnailUrl','thumbnailRelativePath'}
    if not isinstance(response,dict) or set(response)!=keys or any(not isinstance(response[k],str) or len(response[k])>2048 for k in keys):
        raise ValueError('upload response schema changed')
    image=urlsplit(response['imageUrl'])
    if (image.scheme!='https' or image.hostname!=CDN or image.username or image.password or image.port not in (None,443)
            or image.query or image.fragment or not image.path or '..' in image.path.split('/')
            or image.path.lstrip('/') not in (response['imageRelativePath'].lstrip('/'),
                'vlink-storages-prod/thenailhub/'+response['imageRelativePath'].lstrip('/'))):
        raise ValueError('upload response image URL invalid')
    # Thumbnail value is recorded exactly, without claiming it is a validated
    # URL or that its bytes represent the approved source image.
    binding={'version':'official-upload-evidence-v1','status':'UPLOAD_EVIDENCE_NOT_PUBLISHABLE',
             'request_sha256':hashlib.sha256(request_bytes).hexdigest(),
             'source_sha256':hashlib.sha256(source_bytes).hexdigest(),
             'content_type':content_type,'media_type':media_type,'response':response}
    return (packed(binding)+'\n').encode('utf-8')
