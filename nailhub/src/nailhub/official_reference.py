"""Independent parsing of submitted official HTML for review transport."""
from html.parser import HTMLParser
import re
MAX_BYTES = 2_000_000
MAX_TEXT = 150_000
VOID_TAGS = {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}

class MainText(HTMLParser):
    """Plain reference text from exactly one main; omit navigation and ads.

    It is an aid for reviewers, not a lossless extraction of article content.
    The exact HTML remains available alongside it for audit.
    """
    excluded={'script','style','noscript','nav','aside','footer','svg','form','iframe'}
    blocks={'h1','h2','h3','h4','p','li'}
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.main_count=0; self.main_depth=0; self.excluded_depth=0
        self.collect_depth=0; self.buffer=[]; self.paragraphs=[]; self.breadcrumb=False
        self.in_head=False; self.in_title=False; self.title_parts=[]
        self.ad_depth=0

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
        if tag in self.blocks and self.collect_depth:
            self.collect_depth-=1
            if self.collect_depth==0:
                s=' '.join(''.join(self.buffer).split()); self.buffer=[]
                if s and not self.breadcrumb: self.paragraphs.append(s)
                self.breadcrumb=False

    def handle_data(self, data):
        if self.in_head and self.in_title:self.title_parts.append(data)
        if self.main_depth and not self.excluded_depth and not self.ad_depth and self.collect_depth:
            self.buffer.append(data)


def extract(data):
    if not isinstance(data,bytes) or len(data)>MAX_BYTES: raise ValueError('source too large')
    text=data.decode('utf-8-sig',errors='strict')
    parser=MainText(); parser.feed(text); parser.close()
    if parser.main_count!=1 or parser.main_depth or parser.collect_depth or parser.ad_depth or not parser.paragraphs:
        raise ValueError('unsupported or incomplete source structure')
    output='\n\n'.join(parser.paragraphs)+'\n'
    if len(output)>MAX_TEXT: raise ValueError('source text too large')
    return output


def source_title(data):
    parser=MainText();parser.feed(data.decode('utf-8-sig',errors='strict'));parser.close()
    title=' '.join(''.join(parser.title_parts).split())
    if not title or len(title)>500: raise ValueError('source title missing or too long')
    return title
