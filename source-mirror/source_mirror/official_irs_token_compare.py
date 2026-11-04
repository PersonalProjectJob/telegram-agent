"""Prototype only: compare bytes except one Drupal theme token, never publish."""
import hashlib
from html.parser import HTMLParser
import re
from .localization import strict_json
from .official_link_contract import links
from .official_sources import SOURCES,MAX_BYTES,extract,source_title,verify_source_date

SCRIPT=re.compile(rb'<script\b(?P<attrs>[^>]*)>(?P<body>.*?)</script\s*>',re.I|re.S)
TOKEN=re.compile(rb'"theme_token"\s*:\s*"([A-Za-z0-9_-]{43})"')

def normalized(data):
    if not isinstance(data,bytes) or len(data)>MAX_BYTES:raise ValueError('invalid source bytes')
    class Tags(HTMLParser):
        def __init__(self):super().__init__(convert_charrefs=True);self.scripts=[]
        def handle_starttag(self,tag,attrs):
            if tag=='script':self.scripts.append(attrs)
    parser=Tags();parser.feed(data.decode('utf-8-sig',errors='strict'));parser.close()
    matches=list(SCRIPT.finditer(data))
    if len(matches)!=len(parser.scripts):raise ValueError('ambiguous script structure')
    candidates=[]
    for index,(match,attrs) in enumerate(zip(matches,parser.scripts)):
        values=dict(attrs)
        if len(values)!=len(attrs):raise ValueError('duplicate script attributes')
        if values.get('type')=='application/json' and values.get('data-drupal-selector')=='drupal-settings-json':
            candidates.append((index,match))
    if len(candidates)!=1:raise ValueError('Drupal settings script unavailable')
    _,script=candidates[0];body=script.group('body')
    parsed=strict_json(body.decode('utf-8'))
    if not isinstance(parsed,dict) or not isinstance(parsed.get('ajaxPageState'),dict):raise ValueError('Drupal AJAX state unavailable')
    token=parsed['ajaxPageState'].get('theme_token')
    if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}',token):raise ValueError('Drupal theme token invalid')
    found=list(TOKEN.finditer(body))
    if len(found)!=1 or found[0].group(1)!=token.encode('ascii'):raise ValueError('ambiguous Drupal theme token')
    start=script.start('body')+found[0].start(1);end=script.start('body')+found[0].end(1)
    return data[:start]+b'__DRUPAL_THEME_TOKEN_43__'+data[end:]

def compare(old,new):
    item=next(x for x in SOURCES if x['id']=='irs-tip-reporting')
    for data in (old,new):verify_source_date(item,data)
    left=normalized(old);right=normalized(new)
    if (left!=right or source_title(old)!=source_title(new) or extract(old)!=extract(new)
            or links(old,item['url'])!=links(new,item['url'])):raise ValueError('source differs beyond token')
    return {'status':'TOKEN_ONLY_PROTOTYPE_NOT_PUBLISHABLE','old_sha256':hashlib.sha256(old).hexdigest(),
            'new_sha256':hashlib.sha256(new).hexdigest(),'comparison_sha256':hashlib.sha256(left).hexdigest()}
