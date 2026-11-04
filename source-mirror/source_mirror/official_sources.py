"""Fetch fixed US government references into a permanently held review queue.

This module deliberately has no connection to Store.articles or to publishing.
Downloaded references are not publication-ready drafts or expert approvals.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import http.client
from html.parser import HTMLParser
import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import tempfile
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler

SOURCES = (
    {'id':'cdc-niosh-nail-technicians', 'url':'https://www.cdc.gov/niosh/nail-technicians/about/index.html', 'category':'us-workplace-safety', 'scope':'US CDC/NIOSH research and workplace safety guidance; not mandatory OSHA law; applicable federal, state and local requirements require separate review'},
    {'id':'cdc-niosh-2015-bulletin', 'url':'https://www.cdc.gov/niosh/bulletin/2015/nail-salon-workers.html', 'category':'us-workplace-safety', 'scope':'Historical US NIOSH science bulletin posted 2015-05-13; past statistics and guidance are not evidence of current law or current conditions'},
    {'id':'sba-business-plan', 'url':'https://www.sba.gov/counseling/plan-your-business/', 'category':'us-business', 'scope':'General US small-business background; salon context requires human review'},
    {'id':'sba-legacy-business-plan-2025', 'url':'https://legacy.sba.gov/business-guide/plan-your-business/write-your-business-plan', 'category':'us-business', 'scope':'SBA legacy business planning guide last updated 2025-11-13; general US business information, not individualized legal, tax, or financial advice'},
    {'id':'irs-tip-reporting', 'url':'https://www.irs.gov/businesses/small-businesses-self-employed/tip-recordkeeping-and-reporting', 'category':'us-tax', 'scope':'US federal IRS guidance on tip recordkeeping and reporting; state and local requirements may differ'},
    {'id':'irs-worker-classification', 'url':'https://www.irs.gov/businesses/small-businesses-self-employed/independent-contractor-self-employed-or-employee', 'category':'us-tax', 'scope':'US federal IRS guidance on worker classification; state and local requirements may differ'},
    {'id':'cdc-ncezid-nail-hygiene-2024', 'url':'https://www.cdc.gov/hygiene/about/nail-hygiene.html', 'category':'us-general-hygiene', 'scope':'US CDC NCEZID general nail hygiene guidance reviewed 2024-04-16; not a statement of state or local law'},
)
MAX_BYTES = 2_000_000
MAX_TEXT = 150_000
TIMEOUT = 12
VOID_TAGS={'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class MainText(HTMLParser):
    """Plain reference text from exactly one main; omit navigation and ads.

    It is an aid for reviewers, not a lossless extraction of article content.
    The exact HTML remains available alongside it for audit.
    """
    excluded={'script','style','noscript','nav','aside','footer','svg','form','iframe'}
    blocks={'h1','h2','h3','h4','p','li'}
    def __init__(self, citation_boundaries=True):
        super().__init__(convert_charrefs=True)
        self.main_count=0; self.main_depth=0; self.excluded_depth=0
        self.collect_depth=0; self.buffer=[]; self.paragraphs=[]; self.breadcrumb=False
        self.in_head=False; self.in_title=False; self.title_parts=[]
        self.ad_depth=0
        self.citation_boundaries=citation_boundaries
        self._anchor_parts=None; self._previous_numeric_anchor=False; self._anchor_boundary=None

    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if tag=='head': self.in_head=True
        if self.in_head and tag=='title': self.in_title=True
        if tag=='main':
            self.main_count+=1; self.main_depth+=1; return
        if not self.main_depth: return
        if self.ad_depth:
            if tag not in VOID_TAGS:self.ad_depth+=1
            return
        if re.search(r'(?:^|[\s_-])(?:ad|ads|advert|advertisement)(?:$|[\s_-])',attrs.get('class') or '',re.I) and tag not in VOID_TAGS:
            self.ad_depth=1;return
        if self.collect_depth and ('breadcrumb' in (attrs.get('class') or '').lower() or attrs.get('aria-current')=='page'):
            self.breadcrumb=True
        if self.excluded_depth:
            if tag in self.excluded: self.excluded_depth+=1
            return
        if tag in self.excluded:
            self.excluded_depth=1; return
        if tag in self.blocks:
            self.collect_depth+=1
        if self.collect_depth and tag=='a':
            self._anchor_parts=[]
            self._anchor_boundary=len(self.buffer) if self._previous_numeric_anchor else None
        if self.collect_depth and tag=='br': self.buffer.append(' ')

    def handle_endtag(self, tag):
        if tag=='title': self.in_title=False
        if tag=='head': self.in_head=False
        if tag=='main':
            self.main_depth=max(0,self.main_depth-1); return
        if not self.main_depth: return
        if self.ad_depth:
            if tag in VOID_TAGS:return
            self.ad_depth-=1;return
        if self.excluded_depth:
            if tag in self.excluded: self.excluded_depth-=1
            return
        if tag=='a' and self._anchor_parts is not None:
            self._previous_numeric_anchor=''.join(self._anchor_parts).strip().isdigit()
            self._anchor_parts=None; self._anchor_boundary=None
        if tag in self.blocks and self.collect_depth:
            self.collect_depth-=1
            if self.collect_depth==0:
                s=' '.join(''.join(self.buffer).split()); self.buffer=[]
                if s and not self.breadcrumb: self.paragraphs.append(s)
                self.breadcrumb=False
                self._previous_numeric_anchor=False

    def handle_data(self, data):
        if self.in_head and self.in_title:self.title_parts.append(data)
        if self.main_depth and not self.excluded_depth and not self.ad_depth and self.collect_depth:
            if self._anchor_parts is not None:
                if self._anchor_boundary is not None and data:
                    if self.citation_boundaries and data[0].isdigit(): self.buffer.insert(self._anchor_boundary,' ')
                    self._anchor_boundary=None
                self._anchor_parts.append(data)
            elif data:
                self._previous_numeric_anchor=False
            self.buffer.append(data)


def extract(data, *, citation_boundaries=True):
    if not isinstance(data,bytes) or len(data)>MAX_BYTES: raise ValueError('source too large')
    text=data.decode('utf-8-sig',errors='strict')
    parser=MainText(citation_boundaries=citation_boundaries); parser.feed(text); parser.close()
    if parser.main_count!=1 or parser.main_depth or parser.collect_depth or parser.ad_depth or not parser.paragraphs:
        raise ValueError('unsupported or incomplete source structure')
    output='\n\n'.join(parser.paragraphs)+'\n'
    if len(output)>MAX_TEXT: raise ValueError('source text too large')
    return output


def require_no_unbound_links(data):
    """Block eligibility if extracted source text contains unresolved anchors."""
    class LinkAudit(MainText):
        def __init__(self): super().__init__(); self.found=False
        def handle_starttag(self,tag,attrs):
            super().handle_starttag(tag,attrs)
            if tag=='a' and self.main_depth and self.collect_depth and not self.excluded_depth and not self.ad_depth:
                self.found=True
    if not isinstance(data,bytes) or len(data)>MAX_BYTES: raise ValueError('invalid source bytes')
    parser=LinkAudit(); parser.feed(data.decode('utf-8-sig',errors='strict')); parser.close()
    # Also enforce the same structural parser contract as extraction.
    extract(data)
    if parser.found: raise ValueError('source links require verified translation and native binding')


def source_title(data):
    parser=MainText();parser.feed(data.decode('utf-8-sig',errors='strict'));parser.close()
    title=' '.join(''.join(parser.title_parts).split())
    if not title or len(title)>500: raise ValueError('source title missing or too long')
    return title


def verify_source_date(item,data):
    """Bind this historical bulletin's visible posted date to its HTML."""
    if item['id']=='sba-legacy-business-plan-2025':
        class FooterDate(HTMLParser):
            def __init__(self):super().__init__(convert_charrefs=True);self.depth=0;self.parts=[];self.matches=[]
            def handle_starttag(self,tag,attrs):
                if tag=='footer' and 'basic-page__footer' in (dict(attrs).get('class') or '').split():
                    if self.depth:raise ValueError('nested SBA footer')
                    self.depth=1;self.parts=[]
            def handle_data(self,text):
                if self.depth:self.parts.append(text)
            def handle_endtag(self,tag):
                if tag=='footer' and self.depth:self.matches.append(' '.join(''.join(self.parts).split()));self.depth=0
        parser=FooterDate();parser.feed(data.decode('utf-8-sig',errors='strict'));parser.close()
        if (len(parser.matches)!=1 or not re.search(r'\bLast updated November 13, 2025\b',parser.matches[0])
                or source_title(data)!='Write your business plan | U.S. Small Business Administration'):
            raise ValueError('SBA legacy source identity or update date changed')
        return
    if item['id']=='cdc-ncezid-nail-hygiene-2024':
        class HygieneMarkers(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True); self.reviewed=[]; self.dates=[]; self.agencies=[]; self.capture=None; self.parts=[]
            def handle_starttag(self,tag,attrs):
                attrs=dict(attrs)
                if tag=='meta' and attrs.get('property')=='cdc:last_reviewed': self.reviewed.append(attrs.get('content'))
                if tag=='time' and 'cdc-page-title-bar__item--date' in (attrs.get('class') or '').split():
                    self.capture=('date',attrs.get('datetime','')); self.parts=[]
                elif tag=='a' and attrs.get('href')=='/ncezid/index.html': self.capture=('agency',None); self.parts=[]
            def handle_data(self,data):
                if self.capture: self.parts.append(data)
            def handle_endtag(self,tag):
                if self.capture and ((tag=='time' and self.capture[0]=='date') or (tag=='a' and self.capture[0]=='agency')):
                    value=' '.join(''.join(self.parts).split())
                    if self.capture[0]=='date': self.dates.append((self.capture[1],value))
                    else: self.agencies.append(value)
                    self.capture=None
        p=HygieneMarkers(); p.feed(data.decode('utf-8-sig',errors='strict')); p.close()
        if (p.reviewed!=['April 16, 2024'] or p.dates!=[('2024-04-16 04:00:00','April 16, 2024')]
                or p.agencies!=['National Center for Emerging and Zoonotic Infectious Diseases (NCEZID)']
                or source_title(data)!='Healthy Habits: Nail Hygiene | Water, Sanitation, and Environmentally Related Hygiene (WASH) | CDC'):
            raise ValueError('CDC nail hygiene source identity or review date changed')
        return
    if item['id']!='cdc-niosh-2015-bulletin': return
    class DateMarker(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True); self.depth=0; self.parts=[]; self.subtitles=[]; self.times=[]
        def handle_starttag(self,tag,attrs):
            attrs=dict(attrs)
            if self.depth: self.depth+=1
            elif tag=='div' and 'page-subtitle' in (attrs.get('class') or '').split(): self.depth=1; self.parts=[]
            if tag=='time' and 'cdc-page-title-bar__item--date' in (attrs.get('class') or '').split(): self.times.append(attrs.get('datetime',''))
        def handle_endtag(self,tag):
            if self.depth:
                self.depth-=1
                if not self.depth: self.subtitles.append(' '.join(''.join(self.parts).split()))
        def handle_data(self,data):
            if self.depth: self.parts.append(data)
    parser=DateMarker(); parser.feed(data.decode('utf-8-sig',errors='strict')); parser.close()
    if parser.subtitles!=['Posted on May 13, 2015'] or len(parser.times)!=1 or not parser.times[0].startswith('2015-05-13 '):
        raise ValueError('historical bulletin posted date missing or changed')


def fetch(item):
    from .official_deadline import require
    require(TIMEOUT+20)
    # URL selection comes from this fixed table, never from crawled page data.
    if item not in SOURCES: raise ValueError('source not allowlisted')
    from urllib.parse import urlsplit
    url=item['url']; parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.hostname not in {'www.cdc.gov','www.sba.gov','legacy.sba.gov','www.irs.gov'} or parsed.port not in (None,443) or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('invalid source URL')
    addresses=socket.getaddrinfo(parsed.hostname,443,type=socket.SOCK_STREAM)
    if not addresses: raise ValueError('source DNS unavailable')
    for row in addresses:
        if not ipaddress.ip_address(row[4][0]).is_global: raise ValueError('non-public source address')
    target=(addresses[0][4][0],443)
    class PinnedHTTPS(http.client.HTTPSConnection):
        def connect(self):
            raw=socket.create_connection(target,timeout=self.timeout)
            try: self.sock=ssl.create_default_context().wrap_socket(raw,server_hostname=parsed.hostname)
            except BaseException: raw.close(); raise
    connection=PinnedHTTPS(parsed.hostname,timeout=TIMEOUT)
    try:
        connection.request('GET',parsed.path or '/',headers={'User-Agent':'NailhubSourceMirror/1.0 (official reference review)','Accept':'text/html','Connection':'close'})
        response=connection.getresponse()
        if response.status!=200: raise HTTPError(url,response.status,'unexpected source response',response.headers,None)
        if response.headers.get_content_type()!='text/html': raise ValueError('unexpected source content type')
        if int(response.headers.get('Content-Length','0') or '0')>MAX_BYTES: raise ValueError('source too large')
        data=response.read(MAX_BYTES+1)
        if len(data)>MAX_BYTES: raise ValueError('source too large')
        return data
    finally:
        connection.close()


def collect(root, item, downloader=fetch):
    if item not in SOURCES: raise ValueError('source not allowlisted')
    data=downloader(item)
    verify_source_date(item,data)
    text=extract(data)
    digest=hashlib.sha256(data).hexdigest(); text_sha=hashlib.sha256(text.encode('utf-8')).hexdigest()
    directory=Path(root).resolve()/'official-review-queue'/item['id']
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    target=directory/digest
    if target.exists():
        # A second crawl never updates or silently approves an existing snapshot.
        if target.is_symlink() or not target.is_dir() or hashlib.sha256((target/'source.html').read_bytes()).hexdigest()!=digest:
            raise ValueError('existing snapshot integrity mismatch')
        old=json.loads((target/'metadata.json').read_text(encoding='utf-8'))
        version=old.get('extractor_version',1)
        if type(version) is not int or version not in (1,2): raise ValueError('unsupported extractor version')
        text_sha=hashlib.sha256(extract(data,citation_boundaries=version==2).encode('utf-8')).hexdigest()
        if (old.get('status')!='expert_review_required' or old.get('source_sha256')!=digest
                or old.get('source_id')!=item['id'] or old.get('source_url')!=item['url']
                or old.get('category')!=item['category'] or old.get('geographic_scope')!=item['scope'] or old.get('title')!=source_title(data)
                or old.get('reference_text_sha256')!=text_sha
                or old.get('reference_text_sha256')!=hashlib.sha256((target/'reference.txt').read_bytes()).hexdigest()):
            raise ValueError('existing review snapshot altered')
        return {'source':item['id'],'status':'expert_review_required','snapshot':str(target),'unchanged':True}
    temp=Path(tempfile.mkdtemp(prefix='.capture-',dir=directory))
    try:
        fetched=datetime.now(timezone.utc).isoformat()
        metadata={'version':1,'extractor_version':2,'source_id':item['id'],'source_url':item['url'],'category':item['category'],
                  'geographic_scope':item['scope'],'fetched_at_utc':fetched,'source_sha256':digest,
                  'reference_text_sha256':text_sha,'status':'expert_review_required',
                  'review_note':'Reference only; factual verification, media rights and official publishing integration are not yet implemented.',
                  'title':source_title(data)}
        (temp/'source.html').write_bytes(data)
        (temp/'reference.txt').write_text(text,encoding='utf-8',newline='\n')
        (temp/'metadata.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        for path in temp.iterdir():
            path.chmod(0o600)
            with path.open('r+b') as f: os.fsync(f.fileno())
        os.rename(temp,target)
    finally:
        if temp.exists(): shutil.rmtree(temp)
    return {'source':item['id'],'status':'expert_review_required','snapshot':str(target),'unchanged':False}


def collect_versioned(root, item, downloader=fetch):
    """Collect v2 under a separate root; never migrate or overwrite legacy snapshots."""
    versioned_root=Path(root).resolve()/'official-extraction-v2'
    if versioned_root.is_symlink(): raise ValueError('invalid versioned extraction root')
    result=collect(versioned_root,item,downloader)
    return {**result,'root':str(versioned_root)}


def collect_all(root):
    results=[]
    for item in SOURCES:
        try: results.append(collect(root,item))
        except (HTTPError,URLError,OSError,ValueError) as exc:
            status=exc.code if isinstance(exc,HTTPError) else None
            # Never dump URL query strings, bodies, or credential-bearing environment.
            results.append({'source':item['id'],'status':'fetch_failed','error_type':type(exc).__name__,'http_status':status})
    return results


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True)
    args=parser.parse_args(); results=collect_all(args.root)
    print(json.dumps(results,ensure_ascii=False,indent=2))
    if any(x['status']!='expert_review_required' for x in results): raise SystemExit(1)


if __name__=='__main__': main()
