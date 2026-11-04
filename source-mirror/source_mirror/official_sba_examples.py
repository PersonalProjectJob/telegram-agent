"""Bind visible SBA legacy example buttons to the frozen source HTML."""
from html.parser import HTMLParser
from urllib.parse import urljoin

BASE='https://legacy.sba.gov/business-guide/plan-your-business/write-your-business-plan'
BUTTONS=(
    ('Download Rebecca\'s traditional business plan','/document/support-rebeccas-business-plan-template-traditional'),
    ('Download Andrew\'s traditional business plan','/document/support-andrews-business-plan-template-traditional'),
    ('Download Andrew\'s lean business plan','/document/support-andrews-business-plan-template-lean'),
)

def examples(data):
    if not isinstance(data,bytes) or len(data)>3_000_000:raise ValueError('invalid SBA source')
    class Parser(HTMLParser):
        def __init__(self):super().__init__(convert_charrefs=True);self.current=None;self.found=[]
        def handle_starttag(self,tag,attrs):
            if tag=='a':
                if self.current is not None:raise ValueError('nested SBA link')
                pairs=dict(attrs)
                if len(pairs)!=len(attrs):raise ValueError('duplicate SBA link attributes')
                self.current=(pairs.get('href'),[])
        def handle_data(self,text):
            if self.current is not None:self.current[1].append(text)
        def handle_endtag(self,tag):
            if tag=='a' and self.current is not None:
                href,parts=self.current;label=' '.join(''.join(parts).split());self.current=None
                if label in {label for label,_ in BUTTONS}:self.found.append((label,href))
    parser=Parser();parser.feed(data.decode('utf-8-sig',errors='strict'));parser.close()
    if tuple(parser.found)!=BUTTONS:raise ValueError('SBA example buttons changed')
    return [(label,urljoin(BASE,path)) for label,path in BUTTONS]
