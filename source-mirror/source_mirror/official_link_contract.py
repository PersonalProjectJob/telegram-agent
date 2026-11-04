"""Extract and render ordered links in official source text; no publishing."""
import html
import ipaddress
import re
from urllib.parse import urljoin, urlsplit
from .official_sources import MainText, extract, MAX_BYTES


def marker(index):
    result=''; index+=1
    while index:
        index,offset=divmod(index-1,26); result=chr(65+offset)+result
    return result


def links(data,base_url):
    if not isinstance(data,bytes) or len(data)>MAX_BYTES: raise ValueError('invalid source bytes')
    class Parser(MainText):
        def __init__(self): super().__init__(); self.anchor=None; self.links=[]
        def handle_starttag(self,tag,attrs):
            super().handle_starttag(tag,attrs)
            if self.anchor is not None and tag in ('img','svg','picture','video'): self.anchor['visual']=True
            if tag=='a' and self.main_depth and self.collect_depth and not self.excluded_depth and not self.ad_depth:
                if self.anchor is not None: raise ValueError('nested source anchor')
                attributes=dict(attrs)
                if len(attributes)!=len(attrs): raise ValueError('duplicate source anchor attributes')
                href=attributes.get('href'); self.anchor={'href':href,'parts':[],'paragraph':len(self.paragraphs),'visual':False}
                if href is not None: self.buffer.append('⟦'+marker(len(self.links))+'⟧')
        def handle_data(self,value):
            super().handle_data(value)
            if self.anchor is not None and not self.excluded_depth and not self.ad_depth: self.anchor['parts'].append(value)
        def handle_endtag(self,tag):
            prior_count=len(self.paragraphs)
            if tag=='a' and self.anchor is not None:
                anchor=self.anchor; self.anchor=None
                label=' '.join(''.join(anchor['parts']).split()); href=anchor['href']
                if anchor['visual']: raise ValueError('visual source anchor unsupported')
                if href is None:
                    if label: raise ValueError('source link missing href')
                    super().handle_endtag(tag); return
                if not isinstance(href,str) or not href.strip() or href!=href.strip(): raise ValueError('source link missing href')
                url=urljoin(base_url,href); parts=urlsplit(url)
                if parts.scheme in ('https','http'):
                    if (not parts.hostname or parts.username or parts.password
                            or parts.port not in (None,443 if parts.scheme=='https' else 80)):
                        raise ValueError('unsupported source link')
                    host=parts.hostname.lower()
                    if ('%' in host or '\\' in url or any(ord(c)<33 or ord(c)==127 for c in url)
                            or host=='localhost' or host.endswith(('.localhost','.local','.internal'))):
                        raise ValueError('unsupported source link host')
                    try: address=ipaddress.ip_address(host)
                    except ValueError:
                        if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?',host) or '.' not in host or '..' in host:
                            raise ValueError('unsupported source link host')
                        # Numeric-looking hostnames may be alternate IP encodings.
                        if all(re.fullmatch(r'(?:[0-9]+|0x[0-9a-f]+)',label) for label in host.split('.')):
                            raise ValueError('unsupported source link host')
                    else:
                        if not address.is_global: raise ValueError('unsupported source link host')
                elif parts.scheme=='mailto':
                    if (parts.netloc or parts.query or parts.fragment
                            or not re.fullmatch(r'[A-Za-z0-9._+-]+@[A-Za-z0-9.-]+',parts.path)):
                        raise ValueError('unsupported source link')
                else: raise ValueError('unsupported source link')
                self.buffer.append('⟦/'+marker(len(self.links))+'⟧')
                self.links.append((anchor['paragraph'],label,url))
            super().handle_endtag(tag)
            # MainText discards an entire breadcrumb block only on closing it.
            # Roll back anchors from that block as well, before numbering later
            # anchors, so they never masquerade as article links.
            if tag in self.blocks and not self.collect_depth and len(self.paragraphs)==prior_count:
                while self.links and self.links[-1][0]==prior_count: self.links.pop()
    parser=Parser(); parser.feed(data.decode('utf-8-sig',errors='strict')); parser.close()
    if parser.anchor is not None or len(parser.links)>100: raise ValueError('invalid source link structure')
    paragraphs=extract(data).strip().split('\n\n'); result=[]; previous=(-1,-1)
    if any('⟦' in para or '⟧' in para for para in paragraphs): raise ValueError('reserved source marker')
    if len(parser.paragraphs)!=len(paragraphs): raise ValueError('source anchor paragraph mismatch')
    cleaned=[]; positions={}
    for paragraph,tagged in enumerate(parser.paragraphs):
        out=''; cursor=0
        for found in re.finditer(r'⟦([A-Z]+)⟧([^⟦⟧]*)⟦/\1⟧',tagged):
            out+=tagged[cursor:found.start()]; raw=found.group(2); start=len(out)
            left=len(raw)-len(raw.lstrip()); right=len(raw.rstrip()); out+=raw
            positions[found.group(1)]=(paragraph,start+left,start+(right if raw.strip() else left),raw.strip()); cursor=found.end()
        out+=tagged[cursor:]; cleaned.append(' '.join(out.split()))
        for key,(p,start,end,label) in list(positions.items()):
            if p!=paragraph: continue
            prefix=' '.join(out[:start].split())
            normalized_start=len(prefix)+(1 if prefix and out[:start] and out[start:] and out[start-1].isspace() else 0)
            normalized_end=len(' '.join(out[:end].split()))
            if not label: normalized_end=normalized_start
            positions[key]=(p,normalized_start,normalized_end,label)
    if cleaned!=paragraphs or len(positions)!=len(parser.links): raise ValueError('unsupported source anchor structure')
    for index,(paragraph,label,url) in enumerate(parser.links):
        pos=positions.get(marker(index))
        if not pos or pos[0]!=paragraph or pos[3]!=label: raise ValueError('ambiguous source anchor')
        start=pos[1]
        if (paragraph,start)<previous: raise ValueError('source anchor order changed')
        previous=(paragraph,start)
        result.append({'paragraph':paragraph,'start':start,'end':pos[2],'text':label,'url':url})
    return result


def marked_source(data,base_url):
    paragraphs=extract(data).strip().split('\n\n'); contract=links(data,base_url)
    for i in reversed(range(len(contract))):
        link=contract[i]; p=paragraphs[link['paragraph']]; m=marker(i)
        paragraphs[link['paragraph']]=p[:link['start']]+'⟦'+m+'⟧'+link['text']+'⟦/'+m+'⟧'+p[link['end']:]
    return '\n\n'.join(paragraphs)+'\n'


def validate_translation(data,base_url,text,*,source_language=False):
    contract=links(data,base_url); paragraphs=text.strip().split('\n\n') if isinstance(text,str) else []
    if len(paragraphs)!=len(extract(data).strip().split('\n\n')): raise ValueError('source paragraph structure changed')
    tokens=re.findall(r'⟦[^⟧]*⟧',text)
    expected=[token for i in range(len(contract)) for token in ('⟦'+marker(i)+'⟧','⟦/'+marker(i)+'⟧')]
    if tokens!=expected or text.count('⟦')!=len(expected) or text.count('⟧')!=len(expected): raise ValueError('source link markers changed')
    for i,link in enumerate(contract):
        m=marker(i); pattern=re.escape('⟦'+m+'⟧')+r'([^⟦⟧\n]*)'+re.escape('⟦/'+m+'⟧')
        match=re.search(pattern,paragraphs[link['paragraph']])
        if (not match or match.group(1)!=match.group(1).strip()
                or (bool(link['text'])!=bool(match.group(1)))): raise ValueError('source anchor placement changed')
        if re.fullmatch(r'https?://\S+',link['text'],re.I) and match.group(1)!=link['text']:
            raise ValueError('visible source URL changed')
    if source_language and text!=marked_source(data,base_url): raise ValueError('source text changed')
    return contract


def render(data,base_url,text,*,source_language=False):
    contract=validate_translation(data,base_url,text,source_language=source_language); output=[]
    for paragraph in text.strip().split('\n\n'):
        parts=[]; position=0
        for match in re.finditer(r'⟦([A-Z]+)⟧([^⟦⟧\n]*)⟦/\1⟧',paragraph):
            parts.append(html.escape(paragraph[position:match.start()],quote=True).replace('\n','<br>'))
            index=next((i for i in range(len(contract)) if marker(i)==match.group(1)),None)
            if index is None: raise ValueError('unknown source anchor')
            parts.append('<a href="'+html.escape(contract[index]['url'],quote=True)+'" target="_blank" rel="noopener noreferrer">'+html.escape(match.group(2),quote=True)+'</a>')
            position=match.end()
        parts.append(html.escape(paragraph[position:],quote=True).replace('\n','<br>')); output.append('<p>'+''.join(parts)+'</p>')
    return ''.join(output)
