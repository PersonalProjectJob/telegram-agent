"""Read-only, redacted structural comparison; not a freshness decision."""
import argparse
import hashlib
from html.parser import HTMLParser
import json
import re
from pathlib import Path
from .official_link_contract import links
from .official_sources import SOURCES,MAX_BYTES,extract,source_title,verify_source_date

class Buckets(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.main=0;self.script=0;self.head=0;self.script_items=[];self.script_item=None
        self.values={key:hashlib.sha256() for key in ('scripts','head','main','outside')}
    def bucket(self):
        return 'scripts' if self.script else ('main' if self.main else ('head' if self.head else 'outside'))
    def add(self,value):self.values[self.bucket()].update((json.dumps(value,ensure_ascii=False,separators=(',',':'))+'\n').encode('utf-8'))
    def handle_starttag(self,tag,attrs):
        if tag=='head':self.head+=1
        if tag=='main':self.main+=1
        if tag=='script':
            self.script+=1;self.script_item={'attrs':attrs,'text':[]}
        self.add(['start',tag,attrs])
    def handle_startendtag(self,tag,attrs):self.add(['empty',tag,attrs])
    def handle_endtag(self,tag):
        self.add(['end',tag])
        if tag=='script':
            if self.script_item is not None:self.script_items.append(self.script_item);self.script_item=None
            self.script=max(0,self.script-1)
        if tag=='main':self.main=max(0,self.main-1)
        if tag=='head':self.head=max(0,self.head-1)
    def handle_data(self,data):
        if self.script_item is not None:self.script_item['text'].append(data)
        self.add(['data',data])
    def handle_comment(self,data):self.add(['comment',data])

def json_changes(left,right):
    try:old=json.loads(left);new=json.loads(right)
    except (ValueError,TypeError):return {'valid_json':False,'changes':[]}
    changes=[];markers=('token','nonce','csrf','session','ajax','analytics','gtag','google','timestamp','drupal','cache','id')
    def walk(a,b,path):
        if len(changes)>=100:raise ValueError('too many JSON changes')
        if isinstance(a,dict) and isinstance(b,dict) and set(a)==set(b):
            for key in sorted(a):walk(a[key],b[key],path+[str(key)])
        elif isinstance(a,list) and isinstance(b,list) and len(a)==len(b):
            for index,(x,y) in enumerate(zip(a,b)):walk(x,y,path+[str(index)])
        elif a!=b:
            joined='/'.join(path)
            safe_keys={'ajax','ajaxPageState','ajax_page_state','ajaxTrustedUrl','ajaxToken','ajax_token','token','csrf_token','csrfToken','theme','theme_token','libraries','settings','drupalSettings','path','pageState'}
            changes.append({'path_sha256':hashlib.sha256(joined.encode()).hexdigest(),
                'key_markers':[marker for marker in markers if marker.lower() in joined.lower()],
                'safe_path':[part if part in safe_keys else '<redacted>' for part in path],
                'old_type':type(a).__name__,'new_type':type(b).__name__,
                'old_length':len(a) if isinstance(a,str) else None,'new_length':len(b) if isinstance(b,str) else None})
    walk(old,new,[])
    return {'valid_json':True,'changes':changes}

def compare(item,old,new):
    if item not in SOURCES or any(not isinstance(data,bytes) or len(data)>MAX_BYTES for data in (old,new)):
        raise ValueError('invalid comparison inputs')
    for data in (old,new):verify_source_date(item,data)
    parsed=[]
    for data in (old,new):
        obj=Buckets();obj.feed(data.decode('utf-8-sig',errors='strict'));obj.close();parsed.append(obj)
    script_changes=[]
    if len(parsed[0].script_items)==len(parsed[1].script_items):
        for i,(a,b) in enumerate(zip(parsed[0].script_items,parsed[1].script_items)):
            attrs_a=dict(a['attrs']);attrs_b=dict(b['attrs']);text_a=''.join(a['text']);text_b=''.join(b['text'])
            changed=sorted(key for key in set(attrs_a)|set(attrs_b) if attrs_a.get(key)!=attrs_b.get(key))
            if changed or text_a!=text_b:
                markers=('drupalSettings','dataLayer','analytics','google','gtag','nonce','akamai','cloudflare','captcha','token')
                prefix=0
                while prefix<min(len(text_a),len(text_b)) and text_a[prefix]==text_b[prefix]:prefix+=1
                suffix=0
                while suffix<min(len(text_a)-prefix,len(text_b)-prefix) and text_a[-suffix-1]==text_b[-suffix-1]:suffix+=1
                script_changes.append({'index':i,'changed_attribute_names':changed,'text_equal':text_a==text_b,
                    'old_text_length':len(text_a),'new_text_length':len(text_b),'same_prefix_length':prefix,'same_suffix_length':suffix,
                    'old_changed_length':len(text_a)-prefix-suffix,'new_changed_length':len(text_b)-prefix-suffix,
                    'markers':[marker for marker in markers if re.search(re.escape(marker),text_a,re.I)],
                    'has_src':bool(attrs_a.get('src')),'type_is_json':attrs_a.get('type') in ('application/json','application/ld+json'),
                    'id_is_drupal_settings':attrs_a.get('id')=='drupal-settings-json',
                    'selector_is_drupal_settings':attrs_a.get('data-drupal-selector')=='drupal-settings-json',
                    'json_difference':json_changes(text_a,text_b)})
    return {'status':'DIAGNOSTIC_ONLY_NOT_FRESHNESS_PROOF',
            'old_sha256':hashlib.sha256(old).hexdigest(),'new_sha256':hashlib.sha256(new).hexdigest(),
            'bytes_equal':old==new,'title_equal':source_title(old)==source_title(new),
            'reference_equal':extract(old)==extract(new),'ordered_links_equal':links(old,item['url'])==links(new,item['url']),
            'regions_equal':{key:parsed[0].values[key].digest()==parsed[1].values[key].digest() for key in parsed[0].values},
            'script_counts':[len(parsed[0].script_items),len(parsed[1].script_items)],'script_changes':script_changes}

def read(path):
    path=Path(path)
    if not path.is_absolute() or path.is_symlink() or not path.is_file() or path.stat().st_size>MAX_BYTES:raise ValueError('invalid input file')
    value=path.read_bytes()
    if len(value)>MAX_BYTES:raise ValueError('input too large')
    return value

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source-id',required=True);parser.add_argument('--old',required=True);parser.add_argument('--new',required=True)
    args=parser.parse_args();items=[item for item in SOURCES if item['id']==args.source_id]
    if len(items)!=1:raise SystemExit('source unavailable')
    print(json.dumps(compare(items[0],read(args.old),read(args.new))))
