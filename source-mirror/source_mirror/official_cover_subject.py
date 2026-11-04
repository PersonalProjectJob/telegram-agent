"""Derive image prompt data from a hash-bound official draft and source.

This stage does not decide whether an existing cover is usable and never grants
publication. The caller must independently establish media availability.
"""
import hashlib,json
from .localization import strict_json,model_response,validate_review
from .official_draft import source_snapshot
from .official_staging_runner import _read
from .agy_broker import MODEL,EFFORT

def subject(root,source_id,source_sha,draft_sha,editorial):
    from .official_sources import SOURCES
    if source_id not in {item['id'] for item in SOURCES}:raise ValueError('unsupported source')
    for value in (source_sha,draft_sha):
        if not isinstance(value,str) or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):raise ValueError('invalid identity')
    raw=_read(root/'official-drafts',f'{source_id}/{source_sha}/{draft_sha}.json',4000000)
    if hashlib.sha256(raw).hexdigest()!=draft_sha:raise ValueError('draft changed')
    draft=strict_json(raw.decode('utf-8'));snapshot=source_snapshot(root,source_id,source_sha)
    if not isinstance(draft,dict) or draft.get('version')!='official-review-v1' or draft.get('status')!='HOLD' or draft.get('source')!=snapshot or draft.get('model')!=MODEL or draft.get('effort')!=EFFORT:raise ValueError('draft source binding failed')
    candidate=draft.get('candidate');review=draft.get('automated_mqm')
    if not isinstance(candidate,dict) or set(candidate)!={'title_vi','text_vi','uncertainties'} or candidate['uncertainties']!=[]:raise ValueError('invalid candidate')
    for field,kind,expected in (('generation_envelope','official-draft',candidate),('review_envelope','mqm-review',review)):
        envelope=draft.get(field)
        if not isinstance(envelope,dict):raise ValueError('missing evidence')
        if set(envelope)!={'status','response'}:
            from .agy_structured import normalize
            if normalize(envelope.get('original_envelope'),kind)!=envelope:raise ValueError('unbound structured evidence')
        if model_response(envelope)!=expected:raise ValueError('unbound model output')
    validate_review(review)
    # Build the actual text fields with a placeholder image. The image is not
    # part of title/summary; the final payload is checked again at eligibility.
    from .official_payload import build
    from .official_tax import source_is_tax
    from .official_sources import SOURCES
    item=next(x for x in SOURCES if x['id']==source_id)
    provisional={'id':'ai-cover','kind':'generated','sha256':'0'*64,'provenance_url':'https://antigravity.google/','credit':'AI-generated illustration','rights_basis':'generated-ai','rights_evidence_sha256':'0'*64,'alt':'AI-generated illustration related to the article'}
    payload=strict_json(build(raw,[provisional],editorial,allow_official_tax=source_is_tax(item,snapshot['metadata']['title'],snapshot['reference_text'])).decode('utf-8'))
    title=payload['locales']['en']['title'];summary=payload['locales']['en']['summary']
    if not isinstance(title,str) or not title.strip() or len(title)>300 or not isinstance(summary,str) or not summary.strip() or len(summary)>1200:raise ValueError('payload subject unavailable')
    binding={'locale':'en','title':title,'summary':summary,'source_sha256':source_sha,'draft_sha256':draft_sha}
    encoded=json.dumps(binding,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()
    return {**binding,'subject_sha256':hashlib.sha256(encoded).hexdigest()}



def discover_irs_cover(snapshot):
    """Conservative inspection of the bound IRS article, not generic web media."""
    import base64
    from html.parser import HTMLParser
    from urllib.parse import urlsplit
    class Inspector(HTMLParser):
        def __init__(self):
            super().__init__();self.articles=0;self.inside=False;self.depth=0;self.media=False;self.unknown_meta=False
        def handle_starttag(self,tag,attrs):
            a=dict(attrs)
            if tag=='article':
                if self.inside:self.media=True
                if a.get('about')==urlsplit(snapshot['metadata']['source_url']).path:
                    self.articles+=1;self.inside=True;self.depth=1
            elif self.inside and tag not in ('area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'):
                self.depth+=1
            if self.inside and (tag in ('img','picture','video','object','embed','iframe','svg','source','script') or 'url(' in a.get('style','').lower() or any(k in a for k in ('data-src','data-srcset','srcset','background'))):self.media=True
            if tag=='meta' and (a.get('property','').lower()=='og:image' or a.get('name','').lower()=='twitter:image'):
                if a.get('content') not in ('https://www.irs.gov/pub/image/irs_logo.jpg','https://www.irs.gov/pub/image/logo_small.jpg'):self.unknown_meta=True
        def handle_endtag(self,tag):
            if self.inside and tag not in ('area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'):
                self.depth-=1
                if self.depth==0:self.inside=False
    try:
        if snapshot['metadata']['source_id']!='irs-tip-reporting':return 'unknown'
        raw=base64.b64decode(snapshot['source_html_base64'],validate=True)
        if hashlib.sha256(raw).hexdigest()!=snapshot['metadata']['source_sha256']:return 'unknown'
        parser=Inspector();parser.feed(raw.decode('utf-8',errors='replace'));parser.close()
        if parser.articles!=1 or parser.inside or parser.media or parser.unknown_meta:return 'unknown'
        return 'missing'
    except (KeyError,TypeError,ValueError):return 'unknown'


def stage_trusted_irs_cover(root,source_sha,draft_sha,*,policy_path,editorial):
    """Generate a candidate only after refetch and conservative cover inspection."""
    from .official_sources import SOURCES,fetch
    from .irs_cover_entry import stage_irs_cover
    facts=subject(root,'irs-tip-reporting',source_sha,draft_sha,editorial)
    snapshot=source_snapshot(root,'irs-tip-reporting',source_sha)
    item=next(item for item in SOURCES if item['id']=='irs-tip-reporting')
    fresh=fetch(item)
    if not isinstance(fresh,bytes) or hashlib.sha256(fresh).hexdigest()!=source_sha:raise ValueError('source unavailable or changed')
    if discover_irs_cover(snapshot)!='missing':raise ValueError('source cover status unresolved')
    record=stage_irs_cover(root,facts['title'],facts['summary'],'missing',policy_path=policy_path,source_id='irs-tip-reporting',source_sha256=source_sha,draft_sha256=draft_sha)
    return {'subject':facts,'cover':record,'publication_enabled':False}


# Narrow exception for one inspected immutable CDC revision. Both the cover and
# the sole body figure must independently return an explicit unavailable status.
# Any markup change or network ambiguity stays held.
_CDC_NIOSH_INSPECTED_SHA='68ee11e813244d9d8202bdfbfba2ae48d0eb760ba1e1df440f0efcb33a581b1b'
_CDC_NIOSH_IMAGES=('/niosh/media/images/2024/07/GettyImages-188077796.jpg','/niosh/media/images/2024/07/GettyImages-2150879026.jpg')

def _cdc_images_explicitly_unavailable():
    import http.client,ipaddress,socket,ssl
    from .official_deadline import require
    require(50)
    host='www.cdc.gov'
    try:
        addresses=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):return False
        target=(addresses[0][4][0],443)
        class PinnedHTTPS(http.client.HTTPSConnection):
            def connect(self):
                raw=socket.create_connection(target,timeout=self.timeout)
                try:self.sock=ssl.create_default_context().wrap_socket(raw,server_hostname=host)
                except BaseException:raw.close();raise
        for path in _CDC_NIOSH_IMAGES:
            connection=PinnedHTTPS(host,timeout=12)
            try:
                connection.request('GET',path,headers={'User-Agent':'NailhubSourceMirror/1.0 (official reference review)','Accept':'image/*','Connection':'close'})
                if connection.getresponse().status not in (403,404):return False
            finally:connection.close()
        return True
    except (OSError,ValueError,http.client.HTTPException):return False

def _cdc_images_lack_verified_rights():
    # No matching trusted policy record means reuse is unverified, not unlicensed.
    import http.client,ipaddress,socket,ssl,io
    from PIL import Image
    from .official_auto_eligibility import load_media_policy
    from .official_deadline import require
    require(50)
    host='www.cdc.gov'
    try:
        policy=load_media_policy();entries=policy['entries']
        addresses=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):return False
        target=(addresses[0][4][0],443)
        class PinnedHTTPS(http.client.HTTPSConnection):
            def connect(self):
                raw=socket.create_connection(target,timeout=self.timeout)
                try:self.sock=ssl.create_default_context().wrap_socket(raw,server_hostname=host)
                except BaseException:raw.close();raise
        for path in _CDC_NIOSH_IMAGES:
            connection=PinnedHTTPS(host,timeout=12)
            try:
                connection.request('GET',path,headers={'User-Agent':'NailhubSourceMirror/1.0 (official reference review)','Accept':'image/*','Connection':'close'})
                response=connection.getresponse()
                if response.status!=200:return False
                data=response.read(20_000_001)
            finally:connection.close()
            if not data or len(data)>20_000_000:return False
            with Image.open(io.BytesIO(data)) as image:image.verify()
            with Image.open(io.BytesIO(data)) as image:image.load()
            sha=hashlib.sha256(data).hexdigest()
            if any(isinstance(e,dict) and e.get('source_id')=='cdc-niosh-nail-technicians' and e.get('image_sha256')==sha for e in entries):return False
        return True
    except (OSError,ValueError,KeyError,TypeError,http.client.HTTPException):return False

def discover_official_cover(snapshot):
    """Return missing only for an unambiguous, image-free bound main.

    One inspected CDC revision permits AI cover when both images are unavailable or lack verified reuse rights. Unknown media holds,
    including lazy loading and embedded content; no download is simulated.
    """
    import base64
    from html.parser import HTMLParser
    from .official_sources import SOURCES, VOID_TAGS
    class Inspector(HTMLParser):
        def __init__(self):
            super().__init__(); self.count=0; self.depth=0; self.ambiguous=False; self.stack=[]
        def handle_starttag(self, tag, attrs):
            a=dict(attrs)
            if len(a)!=len(attrs): self.ambiguous=True
            if tag=='main':
                self.count+=1
                if self.depth: self.ambiguous=True
                self.depth+=1; self.stack.append(tag); return
            if self.depth and tag not in VOID_TAGS: self.depth+=1; self.stack.append(tag)
            if tag=='meta' and (a.get('property','').lower()=='og:image' or a.get('name','').lower() in ('twitter:image','twitter:image:src')) and (a.get('content') or '').strip(): self.ambiguous=True
            if self.depth and (tag in ('img','picture','video','object','embed','iframe','svg','source','script') or 'url(' in (a.get('style') or '').lower() or any(k in a for k in ('data-src','data-srcset','srcset','background'))): self.ambiguous=True
        def handle_endtag(self,tag):
            if self.depth and tag not in VOID_TAGS:
                if not self.stack or self.stack[-1]!=tag: self.ambiguous=True
                else: self.stack.pop(); self.depth-=1
    try:
        meta=snapshot['metadata']; matches=[x for x in SOURCES if x['id']==meta['source_id'] and x['url']==meta['source_url']]
        if len(matches)!=1: return 'unknown'
        if meta['source_id']=='irs-tip-reporting': return discover_irs_cover(snapshot)
        raw=base64.b64decode(snapshot['source_html_base64'],validate=True)
        if hashlib.sha256(raw).hexdigest()!=meta['source_sha256']: return 'unknown'
        if meta['source_id']=='cdc-niosh-nail-technicians' and meta['source_sha256']==_CDC_NIOSH_INSPECTED_SHA:
            if _cdc_images_explicitly_unavailable():return 'unavailable'
            if _cdc_images_lack_verified_rights():return 'unusable'
            return 'unknown'
        parser=Inspector();parser.feed(raw.decode('utf-8-sig',errors='strict'));parser.close()
        if parser.count!=1 or parser.depth or parser.ambiguous: return 'unknown'
        return 'missing'
    except (KeyError,TypeError,ValueError,UnicodeError): return 'unknown'


def stage_trusted_cover(root,source_id,source_sha,draft_sha,*,policy_path,editorial):
    from .official_sources import SOURCES,fetch
    from .irs_cover_entry import stage_irs_cover
    facts=subject(root,source_id,source_sha,draft_sha,editorial)
    snapshot=source_snapshot(root,source_id,source_sha)
    item=next((item for item in SOURCES if item['id']==source_id),None)
    if item is None: raise ValueError('unsupported source')
    fresh=fetch(item)
    if not isinstance(fresh,bytes) or hashlib.sha256(fresh).hexdigest()!=source_sha: raise ValueError('source unavailable or changed')
    status=discover_official_cover(snapshot)
    if status not in ('missing','unavailable','unusable'): raise ValueError('source cover status unresolved')
    record=stage_irs_cover(root,facts['title'],facts['summary'],status,policy_path=policy_path,source_id=source_id,source_sha256=source_sha,draft_sha256=draft_sha)
    return {'subject':facts,'cover':record,'publication_enabled':False}
