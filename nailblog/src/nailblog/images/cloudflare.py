"""Pinned Workers AI Free-plan adapter, inline generation and final-byte vision review."""
import base64
import http.client
import hashlib
import io
import ipaddress
import json
import math
import re
import socket
import threading
from email.utils import parsedate_to_datetime,format_datetime
from pathlib import Path
from urllib.parse import urlsplit

from ..ai_transport import PinnedHTTPS,read_key,resolve
from . import ImageError
from .provider import GeminiImages

HOST='api.cloudflare.com'
ORIGIN='https://'+HOST
GENERATION='@cf/black-forest-labs/flux-2-klein-4b'
REVIEW='@cf/qwen/qwen3.8-27b'
MAX_REQUEST=2*1024*1024
MAX_RESPONSE=24*1024*1024
REFERENCE_VERSION='geometry-reference-510-webp-v1'
ERROR_RESPONSE_LIMIT=16384


class CloudflareHTTPError(ImageError):
    """Sanitized HTTP rejection plus bounded diagnostics; never a retry instruction."""
    def __init__(self,code,diagnostics):
        super().__init__(code)
        self.diagnostics=dict(diagnostics)


def http_error(response):
    status=response.status
    code={400:'cloudflare_bad_request',401:'cloudflare_auth_failed',403:'cloudflare_permission_denied',
          429:'cloudflare_rate_limited'}.get(status,'cloudflare_provider_failed')
    raw=b'';complete=False
    try:
        while len(raw)<=ERROR_RESPONSE_LIMIT:
            part=response.read1(min(4096,ERROR_RESPONSE_LIMIT+1-len(raw)))
            if not part:complete=True;break
            raw+=part
    except Exception:pass  # Retain the known status even if the error body cannot finish.
    truncated=not complete or len(raw)>ERROR_RESPONSE_LIMIT
    bounded=raw[:ERROR_RESPONSE_LIMIT]
    diagnostic={'http_status':int(status),'error_codes':[],'response_truncated':truncated,
                'response_prefix_sha256' if truncated else 'response_body_sha256':hashlib.sha256(bounded).hexdigest()}
    if not truncated:
        try:
            value=json.loads(bounded)
            rows=value.get('errors',[]) if isinstance(value,dict) else []
            if isinstance(rows,list):
                diagnostic['error_codes']=sorted({r['code'] for r in rows[:16] if isinstance(r,dict) and
                    type(r.get('code')) is int and 0<=r['code']<=2147483647})
        except (ValueError,TypeError,KeyError):pass
    try:
        ray=response.getheader('CF-Ray')
        if isinstance(ray,str) and re.fullmatch(r'[0-9a-fA-F]{16,32}(?:-[A-Za-z]{3})?',ray):diagnostic['cf_ray']=ray
        retry=response.getheader('Retry-After')
        if isinstance(retry,str) and re.fullmatch(r'\d{1,6}',retry) and int(retry)<=604800:
            diagnostic['retry_after_seconds']=int(retry)
        elif isinstance(retry,str) and re.fullmatch(r'[A-Z][a-z]{2}, \d{2} [A-Z][a-z]{2} \d{4} \d{2}:\d{2}:\d{2} GMT',retry):
            when=parsedate_to_datetime(retry)
            if format_datetime(when,usegmt=True)==retry:diagnostic['retry_after_http_date']=retry
    except (ValueError,TypeError,AttributeError):pass
    return CloudflareHTTPError(code,diagnostic)


def endpoint(account,model):
    if not re.fullmatch(r'[0-9a-f]{32}',account) or model not in (GENERATION,REVIEW):
        raise ImageError('cloudflare_account_or_model_invalid')
    return ORIGIN+'/client/v4/accounts/'+account+'/ai/run/'+model


def request(url,payload,headers,timeout):
    parts=urlsplit(url)
    if (parts.scheme!='https' or parts.netloc!=HOST or parts.query or parts.fragment or
            not re.fullmatch(r'/client/v4/accounts/[0-9a-f]{32}/ai/run/@cf/(?:black-forest-labs/flux-2-klein-4b|qwen/qwen3\.8-27b)',parts.path)):
        raise ImageError('invalid_endpoint')
    if not isinstance(payload,bytes) or len(payload)>MAX_REQUEST:
        raise ImageError('request_too_large')
    connection=response=watchdog=None;expired=threading.Event()
    try:
        addresses=[row[4][0] for row in resolve(HOST)]
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise ImageError('invalid_endpoint')
        connection=PinnedHTTPS(HOST,addresses[0],timeout);connection.connect();upstream=connection.sock
        def expire():
            expired.set()
            try:upstream.shutdown(socket.SHUT_RDWR)
            except OSError:pass
        watchdog=threading.Timer(timeout,expire);watchdog.daemon=True;watchdog.start()
        connection.request('POST',parts.path,payload,headers);response=connection.getresponse()
        if response.status!=200:
            raise http_error(response)
        chunks=[];size=0
        while size<=MAX_RESPONSE:
            chunk=response.read1(min(65536,MAX_RESPONSE+1-size))
            if not chunk:break
            chunks.append(chunk);size+=len(chunk)
        if expired.is_set():raise ImageError('cloudflare_timeout')
        if size>MAX_RESPONSE:raise ImageError('response_too_large')
        data=json.loads(b''.join(chunks))
        if not isinstance(data,dict) or data.get('success') is not True or not isinstance(data.get('result'),dict):
            raise ImageError('cloudflare_invalid_response')
        return data['result']
    except ImageError:raise
    except (TimeoutError,socket.timeout):raise ImageError('cloudflare_timeout') from None
    except (OSError,ValueError,TypeError,http.client.HTTPException,AttributeError):
        raise ImageError('cloudflare_timeout' if expired.is_set() else 'cloudflare_provider_failed') from None
    finally:
        if watchdog:watchdog.cancel()
        if response:response.close()
        if connection:connection.close()


def image_envelope(result):
    try:
        encoded=result['image']
        if not isinstance(encoded,str) or len(encoded)>MAX_RESPONSE:raise ValueError()
        if encoded.startswith('data:'):
            match=re.fullmatch(r'data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/]+=*)',encoded)
            if not match:raise ValueError()
            encoded=match.group(2)
        raw=base64.b64decode(encoded,validate=True)
        from PIL import Image
        import io
        with Image.open(io.BytesIO(raw)) as image:
            mime={'PNG':'image/png','JPEG':'image/jpeg','WEBP':'image/webp'}[image.format]
        return {'candidates':[{'finishReason':'STOP','content':{'parts':[{'inlineData':{
            'mimeType':mime,'data':base64.b64encode(raw).decode()}}]}}],
            '_cloudflare':{'estimated':True,'basis':'configured_output_tiles'}}
    except (ValueError,KeyError,TypeError,OSError):raise ImageError('cloudflare_invalid_image') from None


class CloudflareImages:
    def __init__(self,config,*,transport=request,key_reader=read_key):
        self.config,self.transport,self.key_reader=config,transport,key_reader
        self._reference=None

    def reference_binding(self):
        cfg=self.config.cloudflare
        if not cfg.reference_image_file and not cfg.reference_image_sha256:return None
        if self._reference is not None:return self._reference['metadata']
        try:
            path=Path(cfg.reference_image_file)
            if (not path.is_absolute() or any(parent.is_symlink() for parent in (path,*path.parents)) or
                    not 0<path.stat().st_size<=4*1024*1024):raise ValueError()
            raw=path.read_bytes();source_sha=hashlib.sha256(raw).hexdigest()
            if source_sha!=cfg.reference_image_sha256:raise ValueError()
            from .post import decode
            from PIL import Image
            image=decode(raw)
            image.thumbnail((510,510),Image.Resampling.LANCZOS)
            stream=io.BytesIO();image.save(stream,format='WEBP',quality=90,method=6)
            data=stream.getvalue()
            if not 0<len(data)<=512*1024:raise ValueError()
            metadata={'source_sha256':source_sha,'processed_sha256':hashlib.sha256(data).hexdigest(),
                      'dimensions':list(image.size),'processing':REFERENCE_VERSION,'input_tiles':1}
            self._reference={'bytes':data,'metadata':metadata}
            return metadata
        except (OSError,ValueError,TypeError):
            raise ImageError('cloudflare_reference_invalid_or_changed') from None

    def verify_models(self):
        cfg=self.config.image
        if cfg.provider!='cloudflare' or (cfg.model_cover,cfg.model_body,cfg.review_model)!=(GENERATION,GENERATION,REVIEW):
            raise ImageError('cloudflare_fixed_models_required')
        endpoint(self.config.cloudflare.account_id,GENERATION)

    def headers(self):
        path=Path(self.config.image.key_file)
        if not path.is_absolute() or path.is_symlink():raise ImageError('cloudflare_private_key_file_required')
        scope='cloudflare|'+ORIGIN+'|'+self.config.cloudflare.account_id
        key=self.key_reader(str(path),scope,environ={},group_name='tgnailblog')
        if not isinstance(key,str) or not key or any(ord(c)<33 or ord(c)==127 for c in key):
            raise ImageError('cloudflare_key_required')
        return {'Authorization':'Bearer '+key}

    def generation(self,prompt,size,ceiling):
        width,height=(1600,960) if size=='2K' else (1280,720) if size=='1K' else (0,0)
        if not width or len(prompt.encode())>8192:raise ImageError('cloudflare_generation_bounds')
        reference=self.reference_binding()
        body={'prompt':prompt,'width':width,'height':height}
        if reference and 'SUBJECT: NAIL_SWATCH_STICKS.' in prompt.split('ARTICLE BRIEF: ',1)[0]:
            body['prompt']+=(' Reference image 0 is a GEOMETRY GUIDE ONLY: reproduce the physical construction '
                'of each long FLAT transparent plastic handle and exactly ONE almond fingernail-shaped tip at ONE '
                'end only. The opposite end must remain a clear, unpainted flat handle, NEVER a second colored tip. '
                'The small ring hole is in the clear handle end OPPOSITE the colored nail tip. Render these objects '
                'as real editorial photography, not a drawn diagram. Do NOT copy reference object count, colors, '
                'finish, background, lighting, arrangement or any title. The ARTICLE '
                'BRIEF palette, material finish, comparison and placement remain authoritative. '
                'Recolor only the single nail-shaped tip; the entire transparent handle stays unpainted.')
            body['input_image_0']=self._reference['bytes']
        return body

    def review(self,data,prompt,ceiling):
        instruction=GeminiImages.review(None,data,prompt,ceiling)['contents'][0]['parts'][0]['text']
        if 'SUBJECT: NAIL_SWATCH_STICKS.' in prompt.split('ARTICLE BRIEF: ',1)[0]:
            instruction+=(' Topic is FALSE if objects are cylindrical cosmetic tubes, lipstick, bottles or containers '
                      'instead of long flat transparent plastic sample sticks with one nail-shaped polished tip. '
                      'Topic is also FALSE if a stick has painted/colored tips at BOTH ends, a painted or opaque '
                      'handle, or a ring hole on the colored nail tip rather than the opposite clear handle end. '
                      'Verify exactly ONE colored nail-shaped tip per stick and the other end visibly clear. '
                      'For nail-color chart article scenes, blank sticks or detached makeup swatches are not acceptable.')
        return {'messages':[{'role':'user','content':[{'type':'text','text':instruction},
                {'type':'image_url','image_url':{'url':'data:image/webp;base64,'+base64.b64encode(data).decode()}}]}],
                'stream':False,'n':1,'max_completion_tokens':ceiling,'reasoning_effort':'low',
                'chat_template_kwargs':{'enable_thinking':False},'response_format':{'type':'json_object'}}

    def forecast(self,model,body):
        if model==GENERATION:
            if (set(body) not in ({'prompt','width','height'},{'prompt','width','height','input_image_0'}) or
                    (body['width'],body['height']) not in ((1600,960),(1280,720))):
                raise ImageError('cloudflare_generation_bounds')
            reference=self.reference_binding()
            if 'input_image_0' in body and (not reference or body['input_image_0']!=self._reference['bytes']):
                raise ImageError('cloudflare_reference_payload_changed')
            tiles=math.ceil(body['width']/512)*math.ceil(body['height']/512)
            return tiles*26.05+(5.37 if 'input_image_0' in body else 0)
        # This is an allocation estimate, not a fabricated vision token count.
        return (self.config.cloudflare.review_input_allowance*40909+body['max_completion_tokens']*290909)/1e6

    def complete(self,model,body):
        headers=self.headers();url=endpoint(self.config.cloudflare.account_id,model)
        if model==GENERATION:
            self.forecast(model,body)
            boundary='nailblog-cloudflare-multipart-v1'
            fields=[]
            for name in ('prompt','width','height'):
                value=str(body[name])
                if boundary in value:raise ImageError('cloudflare_multipart_invalid')
                fields.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
            if 'input_image_0' in body:
                fields.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="input_image_0"; filename="geometry-reference.webp"\r\nContent-Type: image/webp\r\n\r\n'.encode(),
                               body['input_image_0'],b'\r\n'])
            data=b''.join(fields)+('--'+boundary+'--\r\n').encode()
            headers['Content-Type']='multipart/form-data; boundary='+boundary
            result=self.transport(url,data,headers,self.config.image.timeout_seconds)
            return image_envelope(result)
        data=json.dumps(body).encode()
        if len(data)>MAX_REQUEST:raise ImageError('request_too_large')
        headers['Content-Type']='application/json'
        result=self.transport(url,data,headers,self.config.image.timeout_seconds)
        choices=result.get('choices',[])
        if len(choices)!=1 or choices[0].get('finish_reason')!='stop':
            raise ImageError('cloudflare_review_refused_or_truncated')
        text=choices[0].get('message',{}).get('content')
        if not isinstance(text,str):raise ImageError('cloudflare_invalid_review')
        return {'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':text}]}}],
                '_cloudflare':{'estimated':False,'usage':result.get('usage')}}

    def observed(self,model,envelope,body):
        if model==GENERATION:
            return self.forecast(model,body),{'estimated':True,'basis':'output_512_tile_upper_estimate',
                                            'dimensions':[body['width'],body['height']],
                                            'reference':self.reference_binding() if 'input_image_0' in body else None,'input_tile_neurons':5.37 if 'input_image_0' in body else 0}
        meta=envelope.get('_cloudflare',{}).get('usage')
        if (not isinstance(meta,dict) or any(type(meta.get(name)) is not int or meta[name]<0
                for name in ('prompt_tokens','completion_tokens')) or meta['prompt_tokens']<=0 or
                meta['prompt_tokens']>self.config.cloudflare.review_input_allowance or
                meta['completion_tokens']>body['max_completion_tokens']):
            raise ImageError('cloudflare_review_usage_unknown_or_over_allowance')
        charge=(meta['prompt_tokens']*40909+meta['completion_tokens']*290909)/1e6
        return charge,{'estimated':False,'basis':'reported_tokens_at_documented_neuron_rates','usage':meta,
                       'input_allowance_estimated':self.config.cloudflare.review_input_allowance}
