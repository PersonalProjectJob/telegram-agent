"""Stable admin-title observations and conservative semantic duplicate review."""
import math,time,unicodedata,re,json,os,uuid,hashlib
from datetime import datetime,timezone
from pathlib import Path
from .official_duplicate_scan import _response,MAX_PAGES,MAX_BODY,_scan_timeout,_scan_decode
from .localization import QualityHold,strict_json,model_response,packed,config_check
from .agy_broker import call
from .content_compare import compare, ContentError

class DuplicateReviewHold(QualityHold):
 def __init__(self,message,details):
  super().__init__(message);self.audit_details=details

def _reason_tags(value):
 # Whitelisted diagnostic hints, not a separate decision or verbatim model text.
 value=value.casefold();tags=[]
 for tag,pattern in (('mentions_topic',r'\b(?:topic|subject|theme)\b'),('mentions_practical_angle',r'\b(?:angle|approach|steps|method|practical)\b'),('mentions_audience',r'\b(?:audience|salon|owners?|readers?)\b'),('mentions_uncertainty',r'\b(?:uncertain|unsure|insufficient|cannot|unable)\b')):
  if re.search(pattern,value):tags.append(tag)
 return tags or ['other']

def _safe_id(value):
 # Only recognized identifier shapes are retained; unknown forms are hashed.
 if re.fullmatch(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[0-9a-fA-F]{24}|[0-9a-fA-F]{64}|[0-9]{1,20}',value):return value
 return 'sha256:'+hashlib.sha256(value.encode('utf-8')).hexdigest()

def _hash(value):return hashlib.sha256(packed(value).encode('utf-8')).hexdigest()

def normalized(s):return ' '.join(re.findall(r'\w+',unicodedata.normalize('NFKC',s).casefold()))
def _one(page,deadline):
 def timeout():return _scan_timeout(deadline,QualityHold,'duplicate scan timed out')
 def decode(response,number):
  return _scan_decode(response,number,_response,strict_json,MAX_BODY,QualityHold,'admin list unavailable','admin response too large','admin response invalid')
 with page.expect_response(lambda r:_response(r,1),timeout=timeout()) as received:
  navigation=page.goto('https://nailhub.ai/admin/blogs',wait_until='domcontentloaded',timeout=timeout())
 if not navigation or navigation.status!=200 or page.url!='https://nailhub.ai/admin/blogs':raise QualityHold('admin list unavailable')
 records=[];ids=set();expected=None
 for number in range(1,MAX_PAGES+1):
  if number==1:r=received.value
  else:
   button=page.get_by_role('button',name='Trang sau',exact=True)
   if button.count()!=1 or not button.is_enabled():raise QualityHold('admin page unavailable')
   with page.expect_response(lambda r:_response(r,number),timeout=timeout()) as nxt:button.click(timeout=timeout())
   r=nxt.value
  x=decode(r,number);total=x.get('totalCount');pages=x.get('totalPages');items=x.get('items')
  if (type(total) is not int or total<0 or type(pages) is not int or pages!=math.ceil(total/10) or pages>MAX_PAGES
      or x.get('pageNumber')!=number or type(x.get('hasNextPage')) is not bool or x['hasNextPage']!=(number<pages)
      or type(x.get('hasPreviousPage')) is not bool or x['hasPreviousPage']!=(number>1)
      or not isinstance(items,list) or len(items)!=min(10,max(0,total-(number-1)*10))):raise QualityHold('admin pagination invalid')
  if expected is None:expected=(total,pages)
  elif expected!=(total,pages):raise QualityHold('admin list changed')
  for item in items:
   if not isinstance(item,dict) or not isinstance(item.get('id'),str) or not item['id'] or item['id'] in ids:raise QualityHold('admin item invalid')
   status=item.get('status')
   if status not in ('Published','Draft','Archived'):raise QualityHold('admin status invalid')
   ids.add(item['id'])
   if status!='Published':continue
   translations=item.get('translations')
   if not isinstance(translations,list) or not translations:raise QualityHold('admin content unavailable')
   texts=[];langs=set()
   for tr in translations:
    if not isinstance(tr,dict) or not isinstance(tr.get('languageCode'),str) or not isinstance(tr.get('content'),str) or not tr['content'].strip() or not isinstance(tr.get('title'),str):raise QualityHold('admin content invalid')
    language=tr['languageCode'].upper()
    if language not in ('VI','EN'):raise QualityHold('admin language unsupported')
    if language in langs:raise QualityHold('admin language duplicate')
    langs.add(language);texts.append((language,tr['title'],tr['content']))
   records.append((item['id'],tuple(texts)))
  if number>=pages:break
 if len(ids)!=expected[0]:raise QualityHold('admin list incomplete')
 return expected,tuple(records)

def check_titles(page,titles):
 if not isinstance(titles,(list,tuple)) or not titles or any(not isinstance(t,str) or not t.strip() for t in titles):raise QualityHold('candidate title unavailable')
 def normalize(value):return ' '.join(unicodedata.normalize('NFKC',value).casefold().split())
 wanted={normalize(t) for t in titles}
 deadline=time.monotonic()+90;first=_one(page,deadline);second=_one(page,deadline)
 if first!=second:raise QualityHold('admin list changed between scans')
 matches=[ident for ident,texts in first[1] if any(normalize(title) in wanted for _,title,_ in texts)]
 if matches:raise DuplicateReviewHold('duplicate title',{'decision':'duplicate_title','matched_ids':[_safe_id(x) for x in matches[:20]],'matched_count':len(matches),'scanned':len(first[1])})
 return {'status':'title_absent_nonatomic','scanned':len(first[1])}

def check(page,localized,config,inference=None):
 if not isinstance(localized,dict) or not isinstance(localized.get('locales'),dict):raise QualityHold('candidate unavailable')
 candidates={}
 for lang,code in (('Vietnamese','VI'),('English','EN')):
  fields=localized['locales'].get(lang,{})
  blocks=fields.get('blocks') if isinstance(fields,dict) else None
  if not isinstance(blocks,list) or not blocks:raise QualityHold('candidate content unavailable')
  parts=[]
  for block in blocks:
   if not isinstance(block,dict):raise QualityHold('candidate content invalid')
   if block.get('type')=='html' and isinstance(block.get('html'),str):parts.append(block['html'])
   elif block.get('type')!='image':raise QualityHold('candidate content invalid')
  candidates[code]=''.join(parts)
 return check_bodies(page,candidates)

def check_bodies(page,candidates):
 if not isinstance(candidates,dict) or not all(isinstance(candidates.get(x),str) and candidates[x].strip() for x in ('VI','EN')):raise QualityHold('candidate content unavailable')
 deadline=time.monotonic()+90;first=_one(page,deadline);second=_one(page,deadline)
 if first!=second:raise QualityHold('admin list changed between scans')
 matched=[]
 try:
  for ident,texts in first[1]:
   for lang,title,body in texts:
    if lang in candidates:
     result=compare(candidates[lang],body)
     if result['decision']!='distinct':matched.append((ident,result['decision']))
 except ContentError as exc:raise QualityHold('content comparison unavailable') from exc
 if matched:
  raise DuplicateReviewHold('content duplicate or review hold',{'decision':'content_hold','matched_ids':[_safe_id(x[0]) for x in matched[:20]],'matched_count':len(matched),'scanned':len(first[1])})
 return {'status':'distinct_nonatomic','scanned':len(first[1])}

_REASONS={
 'duplicate title':'duplicate_title',
 'semantic duplicate':'semantic_duplicate',
 'semantic duplicate uncertain':'semantic_uncertain',
 'duplicate review inconsistent':'semantic_inconsistent',
 'duplicate review invalid':'invalid_semantic_response',
 'invalid model JSON':'invalid_model_json',
 'invalid inference success envelope':'invalid_inference_envelope',
 'duplicate scan timed out':'scan_timeout',
 'admin list unavailable':'admin_unavailable',
 'admin page unavailable':'admin_page_unavailable',
 'admin response too large':'admin_response_too_large',
 'admin response invalid':'admin_response_invalid',
 'admin pagination invalid':'admin_pagination_invalid',
 'admin list changed':'admin_list_changed',
 'admin list changed between scans':'admin_list_changed',
 'admin item invalid':'admin_item_invalid',
 'admin titles unavailable':'admin_titles_unavailable',
 'admin title invalid':'admin_title_invalid',
 'admin text too long':'admin_text_too_long',
 'admin list incomplete':'admin_list_incomplete',
 'duplicate list too large for safe review':'duplicate_list_too_large',
 'candidate unavailable':'candidate_unavailable',
 'candidate title unavailable':'candidate_title_unavailable',
}
def audited(root,localized,phase,operation):
 root=Path(root)
 article=localized.get('article_id') if isinstance(localized,dict) else None
 if not isinstance(article,str) or not re.fullmatch(r'[0-9a-f]{64}',article):raise QualityHold('duplicate audit identity invalid')
 if phase not in ('precheck','precreate'):raise QualityHold('duplicate audit phase invalid')
 if root.is_symlink() or not root.is_absolute():raise QualityHold('duplicate audit root invalid')
 directory=root/'reports'/'original-duplicate-check'/article
 for part in (root/'reports',root/'reports'/'original-duplicate-check',directory):
  if part.is_symlink():raise QualityHold('duplicate audit path invalid')
  part.mkdir(exist_ok=True)
  if part.is_symlink() or not part.is_dir() or not part.resolve().is_relative_to(root.resolve()):raise QualityHold('duplicate audit path invalid')
 def record(outcome,code,scanned=None,details=None):
  event={'version':'original-duplicate-audit-v2','article_id':article,'phase':phase,'outcome':outcome,'reason_code':code,'scanned':scanned,'at_utc':datetime.now(timezone.utc).isoformat()}
  if details is not None:event['review']=details
  path=directory/(uuid.uuid4().hex+'.json');fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
  with os.fdopen(fd,'w',encoding='utf8') as stream:json.dump(event,stream,sort_keys=True);stream.flush();os.fsync(stream.fileno())
  for p in (directory,directory.parent,directory.parent.parent):
   fd=os.open(p,os.O_RDONLY|os.O_DIRECTORY)
   try:os.fsync(fd)
   finally:os.close(fd)
 try:result=operation()
 except Exception as exc:
  code=_REASONS.get(str(exc),'other_'+type(exc).__name__)
  details=exc.audit_details if isinstance(exc,DuplicateReviewHold) else None
  record('held',code,details.get('scanned') if details else None,details);raise
 if not isinstance(result,dict) or result.get('status')!='distinct_nonatomic':
  record('held','invalid_check_result');raise QualityHold('duplicate check result invalid')
 record('passed','distinct_nonatomic',result.get('scanned'),result.get('audit_details'));return result

def precheck(root,localized,config):
 from playwright.sync_api import sync_playwright
 root=Path(root)
 def operation():
  with sync_playwright() as pw:
   ctx=pw.chromium.launch_persistent_context(str(root/'browser/nailhub-profile'),headless=True,service_workers='block',timezone_id='Asia/Ho_Chi_Minh')
   try:
    ctx.route('**/*',lambda route:route.continue_() if route.request.method in ('GET','HEAD','OPTIONS') else route.abort('blockedbyclient'))
    if hasattr(ctx,'route_web_socket'):ctx.route_web_socket('**/*',lambda ws:ws.close())
    return check(ctx.new_page(),localized,config)
   finally:ctx.close()
 return audited(root,localized,'precheck',operation)
