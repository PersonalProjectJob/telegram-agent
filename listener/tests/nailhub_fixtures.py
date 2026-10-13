"""Synthetic local fixtures; no authenticated browser evidence or credentials."""
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from tg_triage.nailhub.browser import PRESERVED
from tg_triage.nailhub.config import NailHubConfig
from tg_triage.nailhub.validation import FACT_KINDS, Posting

POST_ID = '11111111-1111-4111-8111-111111111111'
OWNER = 1603321775

def settings(directory, **values):
    return replace(NailHubConfig(str(Path(directory)/'jobs.db'), str(Path(directory)/'profile')), **values)

def raw_post(content='Bao $1200/week', **values):
    data = {key: None for key in PRESERVED}
    data.update(id=POST_ID, title='Hiring nail technician', content=content, status='Draft',
                postingCategory='JobPosting', businessName='Salon', address='123 Main St', city='Atlanta',
                state='GA', zipCode='30301', priceType='Guaranteed', price=6, pricePeriod='Weekly',
                priceNegotiable=False, isVIP=False, isPublic=False, isUrgent=False,
                contactInfo={'name':'Salon', 'phone':'(404) 555-0100', 'mobilePhone':'+1 404 555 0100', 'email':''})
    data.update(values)
    return data

def extracted(original, amount='$1200', period='week', kind='guaranteed', mode='weekly'):
    return {'salary': {'mode':mode, 'kind':kind, 'amount_text':amount, 'period':period,
                       'evidence':{'quote':original}, 'schedule':None, 'rationale':'Cited original salary.'},
            'location':{}, 'facts':{kind:[] for kind in FACT_KINDS}, 'rationale':'Source-backed proposal.'}

def dm(**values):
    user=SimpleNamespace(id=OWNER, username='SotaThao', bot=False, is_self=False)
    data=dict(raw_text='/nailhub scan', out=False, is_private=True, is_group=False, is_channel=False,
              sender_id=OWNER, chat_id=OWNER, fwd_from=None, message=SimpleNamespace(fwd_from=None),
              get_sender=AsyncMock(return_value=user), get_chat=AsyncMock(return_value=user), reply=AsyncMock())
    data.update(values)
    return SimpleNamespace(**data)

class FakeBrowser:
    def __init__(self, post, *, save_error=False, save_mismatch=False, rewrite=None):
        self.post=post; self.calls=[]; self.save_error=save_error; self.save_mismatch=save_mismatch; self.rewrite=rewrite
        self.check_action=lambda:None
    async def __aenter__(self): return self
    async def __aexit__(self,*args): pass
    async def ready(self): self.calls.append('ready')
    async def candidates(self): return [self.post]
    async def read(self,_): self.calls.append('read'); return self.post
    async def edit_and_rewrite(self,post,changes):
        self.check_action();self.calls.append('edit/AI');return post.fields['title'],self.rewrite or post.content
    async def save(self,post,expected):
        self.check_action();self.calls.append('save')
        if self.save_error: raise TimeoutError()
        self.post=Posting(post.id,'Draft',expected['category'],expected['content'],dict(expected['fields']))
        if self.save_mismatch: self.post=replace(self.post,content='changed')
        return self.post
    async def approve(self,post):
        self.check_action();self.calls.append('approve');fields=dict(post.fields)
        preserved=json.loads(fields['_preserved']);preserved['publishedAt']='2026-10-12T10:00:00Z'
        fields['_preserved']=json.dumps(preserved,sort_keys=True,ensure_ascii=False)
        self.post=Posting(post.id,'Approved',post.category,post.content,fields);return self.post
