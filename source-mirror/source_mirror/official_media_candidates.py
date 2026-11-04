"""Discover candidate official-page images for later evidence checks, never rights."""
import base64
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from .official_draft import source_snapshot

MAX_IMAGES=40


class Images(HTMLParser):
    ignored={'nav','aside','footer','script','style','form','iframe','svg'}
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_main=0; self.ignore=0; self.images=[]
    def handle_starttag(self,tag,attrs):
        if tag=='main': self.in_main+=1; return
        if not self.in_main: return
        if tag in self.ignored: self.ignore+=1; return
        if self.ignore or tag!='img': return
        if len(dict(attrs))!=len(attrs): return
        attrs=dict(attrs); self.images.append((attrs.get('src'),attrs.get('alt','')))
    def handle_endtag(self,tag):
        if tag=='main': self.in_main=max(0,self.in_main-1)
        elif self.in_main and tag in self.ignored and self.ignore: self.ignore-=1


def discover(root, source_id, source_sha):
    """Read a frozen snapshot; do not download or assert ownership or safety."""
    snapshot=source_snapshot(root,source_id,source_sha)
    page=snapshot['metadata']['source_url']; origin=urlsplit(page).hostname
    parser=Images(); parser.feed(base64.b64decode(snapshot['source_html_base64'],validate=True).decode('utf-8-sig')); parser.close()
    output=[]; seen=set()
    for raw,alt in parser.images:
        if not isinstance(raw,str) or not raw or not isinstance(alt,str) or len(alt)>1000: continue
        url=urljoin(page,raw); parsed=urlsplit(url)
        if (parsed.scheme!='https' or parsed.hostname!=origin or parsed.username or parsed.password
                or parsed.port not in (None,443) or parsed.query or parsed.fragment): continue
        if url in seen: continue
        seen.add(url); output.append({'url':url,'source_alt':alt,'status':'HOLD_RIGHTS_AND_CONTENT_UNVERIFIED'})
        if len(output)>=MAX_IMAGES: break
    return {'source_id':source_id,'source_sha256':source_sha,'candidates':output}
