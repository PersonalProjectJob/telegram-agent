"""Read-only link contract for the CDC nail hygiene source; not a publish gate."""
import html
import re
from urllib.parse import urljoin
from .official_sources import MainText, extract, MAX_BYTES

BASE='https://www.cdc.gov/hygiene/about/nail-hygiene.html'
TARGETS=(
    ('wash your hands','https://www.cdc.gov/clean-hands/about/index.html'),
    ('commercial settings such as nail salons','https://www.epa.gov/safepestcontrol/preventing-pedicure-foot-spa-infections'),
)

def source_link_contract(data):
    """Return ordered offsets in extracted paragraphs, or reject changed markup.

    This does not establish translated anchor placement or native rendering.
    """
    if not isinstance(data,bytes) or len(data)>MAX_BYTES: raise ValueError('invalid source bytes')
    class Parser(MainText):
        def __init__(self):
            super().__init__(); self.anchor=None; self.links=[]
        def handle_starttag(self,tag,attrs):
            super().handle_starttag(tag,attrs)
            if tag=='a' and self.main_depth and self.collect_depth and not self.excluded_depth and not self.ad_depth:
                if self.anchor is not None: raise ValueError('nested source anchor')
                self.anchor={'href':dict(attrs).get('href'), 'parts':[], 'paragraph':len(self.paragraphs)}
        def handle_data(self,value):
            super().handle_data(value)
            if self.anchor is not None and not self.excluded_depth and not self.ad_depth: self.anchor['parts'].append(value)
        def handle_endtag(self,tag):
            if tag=='a' and self.anchor is not None:
                a=self.anchor; self.anchor=None
                label=' '.join(''.join(a['parts']).split())
                if not isinstance(a['href'],str): raise ValueError('source link missing href')
                self.links.append((a['paragraph'],label,urljoin(BASE,a['href'])))
            super().handle_endtag(tag)
    p=Parser(); p.feed(data.decode('utf-8-sig',errors='strict')); p.close()
    if p.anchor is not None or [(label,url) for _,label,url in p.links]!=list(TARGETS):
        raise ValueError('source link set or order changed')
    paragraphs=extract(data).strip().split('\n\n')
    result=[]
    for paragraph,label,url in p.links:
        if paragraph>=len(paragraphs) or paragraphs[paragraph].count(label)!=1: raise ValueError('ambiguous source anchor')
        start=paragraphs[paragraph].index(label)
        result.append({'paragraph':paragraph,'start':start,'end':start+len(label),'text':label,'url':url})
    return result

def marked_source(data):
    """Mark source anchors in-place for translation and independent MQM."""
    paragraphs=extract(data).strip().split('\n\n')
    links=source_link_contract(data)
    for index in reversed(range(len(links))):
        link=links[index]; paragraph=paragraphs[link['paragraph']]; marker=chr(ord('A')+index)
        paragraphs[link['paragraph']]=(paragraph[:link['start']]+'⟦'+marker+'⟧'+link['text']+'⟦/'+marker+'⟧'+paragraph[link['end']:])
    return '\n\n'.join(paragraphs)+'\n'

def validate_translation(data, text, *, source_language=False):
    """Require one ordered pair per link in the original paragraph."""
    links=source_link_contract(data)
    source_paragraphs=extract(data).strip().split('\n\n')
    if not isinstance(text,str): raise ValueError('translated source missing')
    paragraphs=text.strip().split('\n\n')
    if len(paragraphs)!=len(source_paragraphs): raise ValueError('source paragraph structure changed')
    tokens=re.findall(r'⟦[^⟧]*⟧',text)
    expected=[token for i in range(len(links)) for token in ('⟦'+chr(65+i)+'⟧','⟦/'+chr(65+i)+'⟧')]
    if tokens!=expected or text.count('⟦')!=len(expected) or text.count('⟧')!=len(expected):
        raise ValueError('source link markers missing or changed')
    for i,link in enumerate(links):
        mark=chr(65+i); para=paragraphs[link['paragraph']]
        m=re.search(re.escape('⟦'+mark+'⟧')+r'([^⟦⟧\n]+)'+re.escape('⟦/'+mark+'⟧'),para)
        if not m or not m.group(1).strip() or m.group(1)!=m.group(1).strip():
            raise ValueError('source anchor position or label changed')
    # Distinct CDC terms are not interchangeable. This is a necessary lexical
    # check, not proof that the surrounding translation is medically accurate.
    terms=((r'\bsteriliz(?:e|es|ed|ing|ation)\b',r'\btiệt\s+trùng\b'),
           (r'\bdisinfect(?:s|ed|ing|ion)?\b',r'\bkhử\s+trùng\b'),
           (r'\bsanitiz(?:e|es|ed|ing|ation)\b',r'\b(?:làm\s+vệ\s+sinh|đã\s+vệ\s+sinh)\b'))
    if source_language and text!=marked_source(data): raise ValueError('source text changed')
    if not source_language:
        for source,translated in zip(source_paragraphs,paragraphs):
            for english,vietnamese in terms:
                if re.search(english,source,re.I) and not re.search(vietnamese,translated,re.I):
                    raise ValueError('source hygiene terminology changed')
    return links

def render_marked(data,text, *, source_language=False):
    links=validate_translation(data,text,source_language=source_language)
    paragraphs=text.strip().split('\n\n'); rendered=[]
    for paragraph in paragraphs:
        pieces=[]; pos=0
        for m in re.finditer(r'⟦([A-Z])⟧([^⟦⟧\n]+)⟦/\1⟧',paragraph):
            pieces.append(html.escape(paragraph[pos:m.start()],quote=True).replace('\n','<br>'))
            index=ord(m.group(1))-65
            if index>=len(links): raise ValueError('unexpected source anchor')
            pieces.append('<a href="'+html.escape(links[index]['url'],quote=True)+'" target="_blank" rel="noopener noreferrer">'+html.escape(m.group(2),quote=True)+'</a>')
            pos=m.end()
        pieces.append(html.escape(paragraph[pos:],quote=True).replace('\n','<br>'))
        rendered.append('<p>'+''.join(pieces)+'</p>')
    return ''.join(rendered)
