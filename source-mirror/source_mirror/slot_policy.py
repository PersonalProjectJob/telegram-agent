"""Conservative topic routing for automatic and explicit catch-up slots."""
from datetime import timedelta
import re
BUSINESS_SLOTS=('08:00','16:00','20:00')
NAIL_SLOTS=('12:00',)
_BUSINESS=re.compile(r"\b(?:business|marketing|pricing|payment|payments|payroll|bookkeeping|accounting|financial[ -]+management|balance[ -]+sheets?|cyber[ -]?security|digital[ -]+security|information[ -]+security|tax|taxes|irs|tip[ -]+(?:reporting|recordkeeping)|worker[ -]+classification|licens(?:e|ing)|regulat(?:ion|ory)|client[ -]+retention|workplace[ -]+safety|salon[ -]+(?:management|operations|growth|owner))\b|kinh\s+doanh|thanh\s+toán|giấy\s+phép|thuế|quản\s+lý|vận\s+hành",re.I)
_TECHNIQUE=re.compile(r'\b(?:tutorial|step[ -]by[ -]step|how[ -]to[ -]paint|nail[ -]+art|nail[ -]+designs?)\b|hướng\s+dẫn\s+vẽ',re.I)
_BUSINESS_CONTEXT=re.compile(r"\b(?:grow|growing|open|opening|run|running|manage|managing|build|building|establish|establishing|start|starting|launch|launching|price|prices|payment|payments|operations|growth)\b.{0,70}\b(?:salon|salons|business|owner|clients?)\b|\b(?:salon|salons|business|owners?|clients?)\b.{0,70}\b(?:grow|growth|pricing|price|prices|payment|payments|operations|profits?|revenue)\b",re.I)
_SALON_LOCATION_PLANNING=re.compile(r'\b(?:location|site)\b.{0,55}\bplanning\b.{0,80}\b(?:nail[ -]+salons?|salon[ -]+owners?)\b|\bplanning\b.{0,55}\b(?:location|site)\b.{0,80}\b(?:nail[ -]+salons?|salon[ -]+owners?)\b|\b(?:nail[ -]+salons?|salon[ -]+owners?)\b.{0,55}\b(?:location|site)\b.{0,55}\bplanning\b',re.I)
_INDUSTRY=re.compile(r'\b(?:nail[ -]+(?:salon|salons|care|technicians?|business|services?)|manicurists?)\b|tiệm\s+nail|thợ\s+nail|ngành\s+nail|tiệm\s+móng|thợ\s+làm\s+móng',re.I)
_OTHER_SECTOR=re.compile(r'\b(?:farm(?:ing|s)?|agricultur(?:e|al)|crops?|livestock|carpent(?:er|ry)|roofing|restaurants?|trucking|dental|real[ -]+estate)\b',re.I)
_GENERAL_BUSINESS=re.compile(r'\b(?:business[ -]+plan(?:ning)?|plan[ -]+your[ -]+business|small[ -]+business|business[ -]+tax|tax[ -]+compliance|payments?|payment[ -]+processing|payroll|tip[ -]+(?:recordkeeping|reporting)|worker[ -]+classification|licens(?:e|ing)|regulat(?:ion|ory)|client[ -]+retention|marketing)\b',re.I)
_OPERATIONS=re.compile(r'\b(?:hir(?:e|ing)|recruit(?:ment|ing)?|employees?|staff(?:ing)?|schedul(?:e|es|ing)|location|ventilation|operations?|manag(?:e|ement|ing)|regulat(?:ion|ory)|requirements?|compliance|health|safety|business|owners?|finance|expenses?|pricing|marketing|earn(?:ings?)?|income|salary|salaries|wages?|contracts?|career|licens(?:e|ing))\b|tuyển\s+dụng|nhân\s+viên|lịch\s+làm|địa\s+điểm|thông\s+gió|vận\s+hành|kinh\s+doanh|quản\s+lý',re.I)
_NAIL=re.compile(r'\b(?:nails|manicure|manicurist|pedicure|nail[ -]+(?:art|designs?|tutorial|care|technicians?|salon))\b|móng',re.I)
_NON_BEAUTY=re.compile(r'\b(?:nail[ -]+guns?|carpent(?:er|ry)|roofing|fasteners?)\b',re.I)
_BUILD_TECHNIQUE=re.compile(r'\b(?:build|building|built|construct|constructing)\s+(?:(?!(?:salons?|business(?:es)?|studios?|shops?)\b)[a-z][a-z-]*\s+){0,6}(?:nails?|manicures?|nail[ -]+extensions?)\b(?!\s+(?:salons?|business(?:es)?|studios?|shops?))',re.I)
_TECHNIQUE_BODY=re.compile(r'\bstep\s+1\b.{0,300}\b(?:paint|gel[ -]+polish|top[ -]+coat|nail[ -]+art)\b|\bnail[ -]+art[ -]+tutorial\b|\bapply.{0,35}(?:gel[ -]+polish|top[ -]+coat)\b|các\s+bước\s+vẽ',re.I|re.S)
def title_kind(title,context=''):
 if not isinstance(title,str) or not title.strip() or _NON_BEAUTY.search(title):return 'unknown'
 context=context if isinstance(context,str) else ''
 if _BUILD_TECHNIQUE.search(title):return 'unknown'
 business=bool(_BUSINESS.search(title) or _BUSINESS_CONTEXT.search(title) or _SALON_LOCATION_PLANNING.search(title) or (_INDUSTRY.search(title) and _OPERATIONS.search(title)))
 if business and (_TECHNIQUE.search(title) or _TECHNIQUE_BODY.search(context)):return 'unknown'
 if business:
  if _OTHER_SECTOR.search(title):return 'unknown'
  if _INDUSTRY.search(title) or _GENERAL_BUSINESS.search(title):return 'business'
  return 'business' if _INDUSTRY.search(context[:1200]) else 'unknown'
 if re.search(r'\b(?:nail[ -]+salons?|nail[ -]+technicians?|manicurists?)\b',title,re.I) and not _TECHNIQUE.search(title):return 'unknown'
 if _NAIL.search(title):return 'nail'
 return 'unknown'
def allows(slot,title,context=''):
 if slot is None:return True  # Preserve explicitly requested manual URL workflow.
 kind=title_kind(title,context)
 if slot in NAIL_SLOTS:return kind=='nail'
 if slot in BUSINESS_SLOTS:return kind=='business'
 return False
def next_slot(title,now,context=''):
 kind=title_kind(title,context)
 if kind=='unknown':raise ValueError('source topic is unclear for scheduled slot')
 choices=NAIL_SLOTS if kind=='nail' else BUSINESS_SLOTS
 for slot in choices:
  target=now.replace(hour=int(slot[:2]),minute=0,second=0,microsecond=0)
  if target-now>=timedelta(minutes=37):return slot,target
 slot=choices[0];target=(now+timedelta(days=1)).replace(hour=int(slot[:2]),minute=0,second=0,microsecond=0)
 return slot,target
