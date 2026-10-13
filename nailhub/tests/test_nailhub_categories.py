"""Local policy/protocol regressions; these are not authenticated UI evidence."""
import copy
import json
import tempfile
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from nailhub.browser import Browser, matches_expected, posting_from_data, safe_save_payload, prepare_save_payload
from nailhub.categories import (category_proposal, category_change_valid,
    category_repair_expected, category_repair_valid)
from nailhub.service import Service
from nailhub.validation import NeedsHuman, Posting, proposal
from tests.nailhub_fixtures import FakeBrowser, extracted, raw_post, settings

REPAIR_ID = '03908741-40a8-4579-b941-374a1a9c54d2'


def post(body, title='Neutral', **kw):
    return posting_from_data(raw_post(content=body, title=title, **kw))


def repair_pair():
    source = post('Bán tiệm với giá 60k. Tiệm có thu nhập 30k-35k/tháng.', id=REPAIR_ID)
    current = post('Rewritten body without intent.', title='Rewritten title', id=REPAIR_ID,
                   status='Approved', price=60000, publishedAt='2026-10-13T00:00:00')
    return source, current


class CategoryPolicyTests(unittest.TestCase):
    def test_explicit_intents_and_sale_precedence(self):
        cases = [('Cần tìm thợ nail', 'JobPosting'), ('Hiring nail technician', 'JobPosting'),
                 ('Em tìm việc.', 'Portfolio'), ('Thợ nail cần tìm việc', 'Portfolio'),
                 ('Em muốn sang tiệm làm việc. Em tìm việc.', 'Portfolio'),
                 ('Tôi tìm việc. Em muốn sang tiệm khác làm nails.', 'Portfolio'),
                 ('Nail salon for sale. Hiring nail technician.', 'SalonForSale'),
                 ('Sang nhượng tiệm nail', 'SalonForSale')]
        for text, target in cases:
            with self.subTest(text=text):
                original = post(text, title=text)
                audit = category_proposal(original)
                self.assertEqual(audit['target'], target)
                self.assertEqual(audit['source_hash'], original.fingerprint)
                for item in audit['evidence']:
                    self.assertIn(item['quote'], original.content if item['field']=='content' else original.fields['title'])

    def test_ambiguous_negative_product_buyer_and_unsupported_fail_closed(self):
        for text in ('Tìm tiệm nail để mua', 'Polish products for sale',
                     'Salon for sale. Not for sale anymore.', 'Em tìm việc. Cần thợ nail.'):
            with self.subTest(text=text), self.assertRaises(NeedsHuman) as caught:
                category_proposal(post(text))
            self.assertEqual(caught.exception.details['field'], 'category')
        with self.assertRaises(NeedsHuman):
            category_proposal(post('Hiring nail technician', postingCategory='CommonPosting'))

    def test_category_dependent_price_projection(self):
        for body, category, pay, amount in (
            ('Bán tiệm với giá 60k. Tiệm có thu nhập 30k-35k/tháng.', 'SalonForSale', 'fixed', '60000'),
            ('Nail salon for sale. Hiring staff Salary $1500/week.', 'SalonForSale', 'negotiated', ''),
            ('Em tìm việc. Mong lương $1500/week.', 'Portfolio', 'not_applicable', '')):
            original=post(body)
            with tempfile.TemporaryDirectory() as directory:
                result=proposal(original, extracted(body, amount='malformed'), settings(directory))
            self.assertEqual(result['category'],category)
            self.assertEqual(result['fields']['salary_type'],pay)
            self.assertEqual(result['fields']['salary_amount'],amount)
            expected={**original.snapshot(),'category':category,'fields':result['fields'],
                      'audit':{'category_change':result['category_change']}}
            self.assertTrue(category_change_valid(original,expected))
            expected['audit']['category_change']['source_hash']='forged'
            self.assertFalse(category_change_valid(original,expected))

    def test_approved_projection_preserves_all_but_category_and_required_type(self):
        source,current=repair_pair()
        expected=category_repair_expected(current,source)
        self.assertEqual(expected['category'],'SalonForSale')
        self.assertEqual(expected['fields']['salary_type'],'fixed')
        self.assertEqual(expected['fields']['salary_amount'],'60000')
        self.assertEqual(expected['content'],current.content)
        self.assertEqual(expected['fields']['title'],current.fields['title'])
        self.assertEqual(expected['audit']['category_change']['source_hash'],source.fingerprint)
        self.assertEqual(expected['audit']['category_change']['mutation_hash'],current.fingerprint)
        self.assertTrue(category_repair_valid(current,expected))
        for field,value in [('salary_amount','60001'),('title','Changed'),('phone_other','')]:
            changed=copy.deepcopy(expected);changed['fields'][field]=value
            self.assertFalse(category_repair_valid(current,changed))
        for changed in (replace(current,id='11111111-1111-4111-8111-111111111111'),replace(current,status='Draft')):
            with self.assertRaises(NeedsHuman):category_repair_expected(changed,source)
        with self.assertRaises(NeedsHuman):category_repair_expected(current,replace(source,content='Hiring staff'))

    def test_approved_repair_publication_is_exact_not_approve_transition(self):
        source,current=repair_pair();expected=category_repair_expected(current,source)
        actual=Posting(current.id,'Approved',expected['category'],expected['content'],dict(expected['fields']))
        self.assertTrue(matches_expected(actual,expected,status='Approved'))
        fields=dict(actual.fields);metadata=json.loads(fields['_preserved'])
        metadata['publishedAt']='2026-10-14T00:00:00'
        fields['_preserved']=json.dumps(metadata,sort_keys=True,ensure_ascii=False)
        self.assertFalse(matches_expected(replace(actual,fields=fields),expected,status='Approved'))

    def test_category_save_payload_requires_audited_projection_and_exact_facts(self):
        source,current=repair_pair();expected=category_repair_expected(current,source)
        fields=expected['fields'];data=json.loads(fields['_preserved'])
        data.update(postingCategory='SalonForSale',status='Approved',title=fields['title'],content=expected['content'],
                    priceType='Fixed',price=60000)
        mapping={'salon':'businessName','address':'address','city':'city','state':'state','zip':'zipCode',
                 'phone_primary':'phone','phone_other':'mobilePhone','email':'email'}
        data.update({remote:fields[key] for key,remote in mapping.items()})
        self.assertTrue(safe_save_payload(data,current,expected))
        for key,value in [('priceType','Guaranteed'),('price',60001),('content','Changed'),('status','Draft'),
                          ('postingCategory','Portfolio'),('isVIP',True)]:
            changed={**data,key:value}
            self.assertFalse(safe_save_payload(changed,current,expected))
        forged=copy.deepcopy(expected);forged['audit']['category_change']['source_hash']='forged'
        self.assertFalse(safe_save_payload(data,current,forged))
        seeker=post('Em tìm việc.');fields=dict(seeker.fields)
        fields.update(salary_type='not_applicable',salary_amount='')
        wanted={**seeker.snapshot(),'category':'Portfolio','fields':fields,
                'audit':{'category_change':category_proposal(seeker)}}
        payload=json.loads(fields['_preserved'])
        payload.update({remote:fields[key] for key,remote in mapping.items()})
        payload.update(postingCategory='Portfolio',status='Draft',title=fields['title'],content=seeker.content,
                       priceType=None,price=None)
        self.assertTrue(safe_save_payload(payload,seeker,wanted))
        self.assertFalse(safe_save_payload({**payload,'price':0},seeker,wanted))

    def test_category_only_null_start_date_preserves_actual_stored_past_day(self):
        source,current=repair_pair();fields=dict(current.fields);metadata=json.loads(fields['_preserved'])
        metadata['startDate']='2026-10-12T00:00:00'
        fields['_preserved']=json.dumps(metadata,sort_keys=True,ensure_ascii=False)
        current=replace(current,fields=fields);expected=category_repair_expected(current,source)
        payload=dict(metadata)
        mapping={'salon':'businessName','address':'address','city':'city','state':'state','zip':'zipCode',
                 'phone_primary':'phone','phone_other':'mobilePhone','email':'email'}
        payload.update({remote:fields[key] for key,remote in mapping.items()})
        payload.update(postingCategory='SalonForSale',status='Approved',title=fields['title'],content=current.content,
                       priceType='Fixed',price=60000)
        for representation in ('source','null','omitted'):
            ui=dict(payload)
            if representation=='null':ui['startDate']=None
            if representation=='omitted':ui.pop('startDate')
            normalized=prepare_save_payload(ui,current,expected)
            self.assertIsNone(normalized['startDate'])
            self.assertTrue(safe_save_payload(normalized,current,expected))
            self.assertEqual(json.loads(expected['fields']['_preserved'])['startDate'],'2026-10-12T00:00:00')
        with self.assertRaises(NeedsHuman):
            prepare_save_payload({**payload,'startDate':'2026-10-14T00:00:00'},current,expected)
        changed=copy.deepcopy(expected)
        badmeta=json.loads(changed['fields']['_preserved']);badmeta['startDate']='2026-10-14T00:00:00'
        changed['fields']['_preserved']=json.dumps(badmeta,sort_keys=True,ensure_ascii=False)
        with self.assertRaises(NeedsHuman):prepare_save_payload(payload,current,changed)
        actual=Posting(current.id,'Approved',expected['category'],current.content,expected['fields'])
        self.assertTrue(matches_expected(actual,expected,status='Approved'))
        self.assertFalse(matches_expected(replace(actual,fields=changed['fields']),expected,status='Approved'))
        # Ordinary Draft flow has no category-only exception to date preservation.
        draft=replace(current,status='Draft');normal={**draft.snapshot()}
        normal_payload={**payload,'postingCategory':'JobPosting','status':'Draft','priceType':'Guaranteed','startDate':None}
        with self.assertRaises(NeedsHuman):prepare_save_payload(normal_payload,draft,normal)


class CategoryServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_category_translation_settles_then_original_title_restored(self):
        # Simulates the actual form callback protocol, not a live-browser claim.
        import asyncio
        original=post('Nail salon for sale.',title='Original title')
        title=SimpleNamespace(input_value=AsyncMock(return_value='Translated category title'),fill=AsyncMock())
        option=SimpleNamespace(wait_for=AsyncMock(),count=AsyncMock(return_value=1),click=AsyncMock())
        trigger=SimpleNamespace(click=AsyncMock(),inner_text=AsyncMock(return_value='Sang nhượng tiệm'))
        class Response:
            async def __aenter__(self):
                self.value=asyncio.get_running_loop().create_future();self.value.set_result(object());return self
            async def __aexit__(self,*args):pass
        with tempfile.TemporaryDirectory() as directory:
            page=SimpleNamespace(get_by_role=Mock(return_value=option),expect_response=lambda *args,**kw:Response())
            browser=Browser(settings(directory),page=page);browser.one=AsyncMock(return_value=trigger)
            browser._field=AsyncMock(return_value=title);browser._json=AsyncMock(return_value={'translations':['Translated category title']})
            await browser.select_category(original,'SalonForSale')
            page.get_by_role.assert_called_once_with('option',name='Sang nhượng tiệm',exact=True)
            title.fill.assert_awaited_once_with('Original title')
            self.assertEqual(title.input_value.await_count,1)

    async def test_draft_sale_and_seeker_flow_without_content_verification(self):
        for body,target in [('Bán tiệm với giá 60k.','SalonForSale'),('Em tìm việc.','Portfolio')]:
            with self.subTest(body=body), tempfile.TemporaryDirectory() as directory:
                original=post(body);browser=FakeBrowser(original)
                model=SimpleNamespace(extract=Mock(return_value=extracted(body)),verify=Mock(side_effect=AssertionError('unused')))
                service=Service(settings(directory,mode='live',verified=True,verification_ref='offline fake'),model=model,
                                browser_factory=lambda _:browser)
                await service.scan()
                self.assertEqual(service.state.get(original.id)['state'],'approved')
                self.assertEqual(browser.post.category,target)
                model.verify.assert_not_called()

    async def test_approved_repair_checkpoint_idle_lock_and_no_ai_approve(self):
        with tempfile.TemporaryDirectory() as directory:
            source,current=repair_pair();service=Service(settings(directory),model=Mock())
            state=service.state;state.observe(source.id,source.fingerprint,source.snapshot());state.mark(source.id,'approved')
            browser=SimpleNamespace(read=AsyncMock(return_value=current))
            async def repair(fresh,expected):
                self.assertEqual(state.get(source.id)['state'],'category_before_save')
                self.assertTrue(state.status()['running'])
                return Posting(fresh.id,'Approved',expected['category'],expected['content'],expected['fields'])
            browser.category_only_repair=AsyncMock(side_effect=repair)
            with self.assertRaises(NeedsHuman):await service.repair_category(browser,source.id)
            state.pause(True)
            with state.connection() as db:db.execute('UPDATE control SET running=1')
            with self.assertRaises(NeedsHuman):await service.repair_category(browser,source.id)
            state.finish_scan('idle')
            result=await service.repair_category(browser,source.id)
            self.assertEqual(result.category,'SalonForSale');self.assertFalse(state.status()['running'])
            self.assertEqual(state.get(source.id)['state'],'approved')
            self.assertEqual(json.loads(state.get(source.id)['original'])['content'],source.content)
            service.model.assert_not_called()
            browser.category_only_repair.assert_awaited_once()

    async def test_category_repair_unknown_outcome_locked_and_reconciled(self):
        with tempfile.TemporaryDirectory() as directory:
            source,current=repair_pair();service=Service(settings(directory),model=Mock());state=service.state
            state.observe(source.id,source.fingerprint,source.snapshot());state.mark(source.id,'approved');state.pause(True)
            browser=SimpleNamespace(read=AsyncMock(return_value=current),category_only_repair=AsyncMock(side_effect=TimeoutError()))
            with self.assertRaises(TimeoutError):await service.repair_category(browser,source.id)
            self.assertEqual(state.get(source.id)['state'],'uncertain');self.assertFalse(state.status()['running'])
            with self.assertRaises(NeedsHuman):await service.repair_category(browser,source.id)
            browser.category_only_repair.assert_awaited_once()
            expected=json.loads(state.get(source.id)['expected'])
            state.mark(source.id,'category_before_save')
            browser.read=AsyncMock(return_value=Posting(current.id,'Approved',expected['category'],expected['content'],expected['fields']))
            await service.reconcile(browser)
            self.assertEqual(state.get(source.id)['state'],'approved')
