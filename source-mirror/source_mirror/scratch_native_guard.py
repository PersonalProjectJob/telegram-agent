"""Bind native upload requests, their responses, and Create media objects."""
import hashlib,re
from email import policy
from email.parser import BytesParser
from urllib.parse import urlsplit

CDN='vlink-storages-prod.sgp1.cdn.digitaloceanspaces.com'
KEYS={'imageUrl','imageRelativePath','thumbnailUrl','thumbnailRelativePath'}

def bind_upload(request_bytes,content_type,source_bytes,response):
 if not isinstance(request_bytes,bytes) or not isinstance(source_bytes,bytes) or not 0<len(source_bytes)<=20*1024*1024 or len(request_bytes)>20*1024*1024+32768: raise ValueError('upload size invalid')
 if not isinstance(content_type,str) or not re.fullmatch(r'multipart/form-data\s*;\s*boundary=(?:[A-Za-z0-9\x27()+_,./:=?\-]{1,70}|"[A-Za-z0-9\x27()+_,./:=?\-]{1,70}")',content_type,re.I):raise ValueError('invalid multipart type')
 m=BytesParser(policy=policy.default).parsebytes(('Content-Type: '+content_type+'\r\nMIME-Version: 1.0\r\n\r\n').encode('ascii')+request_bytes)
 parts=list(m.iter_parts()) if m.is_multipart() else []
 if m.defects or len(parts)!=1:raise ValueError('invalid upload multipart')
 p=parts[0]; params=p.get_params(header='content-disposition',unquote=True) or []
 if (p.defects or p.is_multipart() or len(p.get_all('Content-Disposition',[]))!=1 or len(p.get_all('Content-Type',[]))!=1 or p.get_all('Content-Transfer-Encoding',[]) or set(k.lower() for k in p.keys())!={'content-disposition','content-type'} or p.get_content_disposition()!='form-data' or p.get_param('name',header='content-disposition')!='file' or len(params)!=3 or {str(k).lower() for k,_ in params[1:]}!={'name','filename'}):raise ValueError('invalid multipart file')
 filename=p.get_filename()
 if not isinstance(filename,str) or not filename or len(filename)>255 or any(c in filename for c in ('/','\\','\x00','\r','\n')):raise ValueError('invalid filename')
 typ=p.get_content_type(); allowed=(typ=='image/jpeg' and source_bytes.startswith(b'\xff\xd8\xff')) or (typ=='image/png' and source_bytes.startswith(b'\x89PNG\r\n\x1a\n')) or (typ=='image/webp' and source_bytes[:4]==b'RIFF' and source_bytes[8:12]==b'WEBP')
 if not allowed or p.get_payload(decode=True)!=source_bytes:raise ValueError('upload bytes changed')
 if not isinstance(response,dict) or set(response)!=KEYS or any(not isinstance(response[k],str) or len(response[k])>2048 for k in KEYS) or not response['imageRelativePath'] or bool(response['thumbnailUrl'])!=bool(response['thumbnailRelativePath']):raise ValueError('invalid upload response')
 for ukey,pkey in (('imageUrl','imageRelativePath'),('thumbnailUrl','thumbnailRelativePath')):
  if not response[ukey]:
   if ukey=='imageUrl':raise ValueError('missing image URL')
   continue
  u=urlsplit(response[ukey]);relative=response[pkey]
  if u.scheme!='https' or u.hostname!=CDN or u.username or u.password or u.port not in (None,443) or u.query or u.fragment or '..' in u.path.split('/') or u.path.lstrip('/') not in (relative.lstrip('/'),'vlink-storages-prod/thenailhub/'+relative.lstrip('/')):raise ValueError('invalid upload URL')
 return {'source_sha256':hashlib.sha256(source_bytes).hexdigest(),'request_sha256':hashlib.sha256(request_bytes).hexdigest(),'response':dict(response),'source_bytes':source_bytes}

def verify_media(sent,cover_receipt,gallery_receipts,cover_sha,gallery_sha):
 from . import scratch_cdn_readback as cdn
 if not isinstance(sent,dict) or not isinstance(sent.get('images'),list): raise ValueError('invalid native media')
 objects=[sent.get('coverImage'),*sent['images']];receipts=[cover_receipt,*gallery_receipts]; hashes=[cover_sha,*gallery_sha]
 if len(objects)!=len(receipts) or len(receipts)!=len(hashes):raise ValueError('native media count changed')
 ids=[]
 for obj,receipt,expected in zip(objects,receipts,hashes):
  if not isinstance(obj,dict) or set(obj)!=KEYS|{'id'} or not isinstance(obj['id'],str) or not obj['id'] or receipt.get('source_sha256')!=expected or any(obj[k]!=receipt['response'][k] for k in KEYS):raise ValueError('native media or receipt changed')
  original=receipt.get('source_bytes')
  if not isinstance(original,bytes) or hashlib.sha256(original).hexdigest()!=expected:raise ValueError('original image changed')
  if cdn.readback(receipt['response']['imageUrl'])!=200:raise ValueError('CDN image unavailable')
  if receipt['response']['thumbnailUrl'] and cdn.readback(receipt['response']['thumbnailUrl'])!=200:raise ValueError('CDN thumbnail unavailable')
  ids.append(obj['id'])
 if len(set(ids[1:]))!=len(ids[1:]):raise ValueError('duplicate gallery id')
