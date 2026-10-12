"""Fakes exercise fail-closed policy only; they are not live browser evidence."""
import asyncio
import json
import tempfile
import threading
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from tg_triage.nailhub.browser import Browser, canonical_text, matches_expected, posting_from_data, prepare_save_payload, same_form_value
from tg_triage.nailhub.service import Service, model_call
from tg_triage.nailhub.validation import NeedsHuman, proposal
from tests.nailhub_fixtures import FakeBrowser, POST_ID, extracted, raw_post, settings

class BrowserContractTests(unittest.IsolatedAsyncioTestCase):
    def test_form_phone_formatting_preserves_number_extension_and_multiplicity(self):
        for key in ('phone_primary', 'phone_other'):
            for value, source in [('951-237 3355', '9512373355'),
                                  ('+1 (951) 237-3355 ext. 12', '9512373355 x12'), ('', '')]:
                with self.subTest(key=key,value=value):
                    self.assertTrue(same_form_value(key,value,source))
            for value, source in [('9512373356','9512373355'),
                                  ('9512373355 x13','9512373355 x12'),
                                  ('9512373355','9512373355 x12'),
                                  ('','9512373355'),
                                  ('9512373355;9512373355','9512373355')]:
                with self.subTest(key=key,value=value):
                    self.assertFalse(same_form_value(key,value,source))
            for value in ('call 9512373355', '951237335', '9512373355 unknown'):
                with self.subTest(key=key,value=value):
                    with self.assertRaises(NeedsHuman):same_form_value(key,value,value)
        self.assertFalse(same_form_value('zip','303 01','30301'))

    async def test_edit_form_phone_formatting_reaches_ai_but_changed_phone_stops(self):
        class Response:
            async def __aenter__(self):
                self.value=asyncio.get_running_loop().create_future();self.value.set_result(object())
                return self
            async def __aexit__(self,*args):pass
        with tempfile.TemporaryDirectory() as directory:
            post=posting_from_data(raw_post(content='AI paragraph1\nAI paragraph2'))
            for phone, allowed in [('+1 404-555 0100',True),('404-555 0101',False),
                                   ('404-555 0100 x12',False),('call 4045550100',False)]:
                with self.subTest(phone=phone):
                    page=SimpleNamespace(expect_response=lambda *args,**kwargs:Response())
                    browser=Browser(settings(directory,mode='live',verified=True,verification_ref='offline fake'),page=page)
                    browser.read=AsyncMock(return_value=post);browser.action_click=AsyncMock();browser._json=AsyncMock()
                    async def field(key):
                        value=phone if key=='phone_primary' else post.fields.get(key,'')
                        return SimpleNamespace(input_value=AsyncMock(return_value=value),
                            inner_text=AsyncMock(return_value=value),
                            get_by_text=lambda _:SimpleNamespace(wait_for=AsyncMock()))
                    browser._field=field
                    browser.control=AsyncMock(return_value=SimpleNamespace(wait_for=AsyncMock(),
                        is_enabled=AsyncMock(return_value=True),inner_text=AsyncMock(return_value=post.content.replace('\n','\n\n')+'\n')))
                    if allowed:
                        self.assertEqual(await browser.edit_and_rewrite(post,{}),(post.fields['title'],post.content))
                        browser.action_click.assert_any_await('ai')
                    else:
                        with self.assertRaises(NeedsHuman):await browser.edit_and_rewrite(post,{})
                        self.assertEqual(browser.action_click.await_args_list,[unittest.mock.call('edit')])

    async def test_preview_mutation_entrypoints_fail_before_page_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            browser=Browser(settings(directory),page=Mock());post=posting_from_data(raw_post())
            for call in (browser.edit_and_rewrite(post,{}),browser.save(post,post.snapshot()),browser.approve(post)):
                with self.assertRaises(NeedsHuman):await call
            browser.page.assert_not_called()

    async def test_verified_live_wrong_status_category_identity_block(self):
        with tempfile.TemporaryDirectory() as directory:
            browser=Browser(settings(directory,mode='live',verified=True,verification_ref='offline fake'),page=Mock())
            for post in (posting_from_data(raw_post(status='Approved')),posting_from_data(raw_post(postingCategory='SalonForSale'))):
                with self.assertRaises(NeedsHuman):await browser.edit_and_rewrite(post,{})
            with self.assertRaises(NeedsHuman):await browser.read('title-as-id')

    async def test_ambiguous_selector_never_first_match(self):
        with tempfile.TemporaryDirectory() as directory:
            locator=SimpleNamespace(count=AsyncMock(return_value=2))
            browser=Browser(settings(directory),page=SimpleNamespace(locator=lambda _:locator))
            with self.assertRaises(NeedsHuman):await browser.one('button')

    async def test_render_readiness_wait_precedes_final_exact_count(self):
        with tempfile.TemporaryDirectory() as directory:
            count=AsyncMock(side_effect=[0,1]);locator=SimpleNamespace(count=count,wait_for=AsyncMock(),is_visible=AsyncMock(return_value=True))
            browser=Browser(settings(directory),page=SimpleNamespace(locator=lambda _:locator))
            self.assertIs(await browser.one('dialog'),locator)
            locator.wait_for.assert_awaited_once_with(state='visible',timeout=30000)

    async def test_action_control_await_then_pause_prevents_ai_click(self):
        with tempfile.TemporaryDirectory() as directory:
            browser=Browser(settings(directory),page=Mock());click=AsyncMock();paused=False
            async def control(_):
                nonlocal paused;paused=True;return SimpleNamespace(click=click)
            def check():
                if paused:raise NeedsHuman('paused')
            browser.control=control;browser.check_action=check
            with self.assertRaises(NeedsHuman):await browser.action_click('ai')
            click.assert_not_awaited()

    async def test_save_guard_ignores_unrelated_request_and_aborts_changed_source(self):
        with tempfile.TemporaryDirectory() as directory:
            browser=Browser(settings(directory,mode='live',verified=True,verification_ref='fake'),page=Mock())
            post=posting_from_data(raw_post());browser.assert_fresh=AsyncMock(side_effect=NeedsHuman('changed'))
            for url,method in [('https://evil.invalid/api/v1/Admin/postings/'+post.id,'PUT'),
                               ('https://api.nailhub.ai/api/v1/Admin/postings/other','PUT'),
                               ('https://api.nailhub.ai/api/v1/Admin/postings/'+post.id,'GET')]:
                route=SimpleNamespace(request=SimpleNamespace(url=url,method=method),abort=AsyncMock(),continue_=AsyncMock())
                await browser.guard_save(route,post,post.snapshot());route.abort.assert_not_awaited();route.continue_.assert_awaited_once_with()
            browser.assert_fresh.assert_not_awaited()
            payload={'postingCategory':post.category,'status':post.status,'title':post.fields['title'],'content':post.content,
                'businessName':'Salon','address':'123 Main St','city':'Atlanta','state':'GA','zipCode':'30301',
                'phone':post.fields['phone_primary'],'mobilePhone':post.fields['phone_other'],'email':'',
                'priceType':'Guaranteed','price':6}
            route=SimpleNamespace(request=SimpleNamespace(url='https://api.nailhub.ai/api/v1/Admin/postings/'+post.id,
                method='PUT',post_data_json=payload),abort=AsyncMock(),continue_=AsyncMock())
            await browser.guard_save(route,post,post.snapshot());route.abort.assert_awaited_once();route.continue_.assert_not_awaited()
            browser.assert_fresh.assert_awaited_once_with(post)

    async def test_pause_during_approve_read_prevents_new_click(self):
        with tempfile.TemporaryDirectory() as directory:
            browser=Browser(settings(directory,mode='live',verified=True,verification_ref='fake'),page=Mock())
            post=posting_from_data(raw_post());paused=False;click=AsyncMock()
            async def read(_):
                nonlocal paused;paused=True;return post
            def check():
                if paused:raise NeedsHuman('paused')
            browser.read=read;browser.control=AsyncMock(return_value=SimpleNamespace(click=click));browser.check_action=check
            with self.assertRaises(NeedsHuman):await browser.approve(post)
            click.assert_not_awaited()

    async def test_changed_source_during_inference_prevents_put(self):
        with tempfile.TemporaryDirectory() as directory:
            browser=Browser(settings(directory,mode='live',verified=True,verification_ref='fake'),page=Mock())
            browser.authenticated=AsyncMock();browser.assert_fresh=AsyncMock(side_effect=NeedsHuman('changed'));browser.control=AsyncMock()
            post=posting_from_data(raw_post())
            with self.assertRaises(NeedsHuman):await browser.save(post,post.snapshot())
            browser.control.assert_not_awaited();browser.page.route.assert_not_called()

    async def test_preview_network_guard_aborts_only_site_mutations(self):
        with tempfile.TemporaryDirectory() as directory:
            browser=Browser(settings(directory),page=Mock())
            for url,method,blocked in [('https://api.nailhub.ai/api/v1/Admin/postings/a','PUT',True),
                    ('https://api.nailhub.ai/api/v1/AI/AIEnhanced','POST',True),
                    ('https://api.nailhub.ai/api/v1/Admin/postings','GET',False),
                    ('https://api.nailhub.ai/api/v1/authentication/signin','POST',False)]:
                route=SimpleNamespace(request=SimpleNamespace(url=url,method=method),abort=AsyncMock(),continue_=AsyncMock())
                await browser._preview_guard(route);self.assertEqual(route.abort.await_count,int(blocked))

    def test_payload_preserves_returned_omitted_fields_rejects_conflict_never_invents_unknown(self):
        post=posting_from_data(raw_post());expected=post.snapshot();expected['fields']=dict(post.fields)
        data={'postingCategory':post.category,'status':post.status,'title':post.fields['title'],'content':post.content,
              'businessName':'Salon','address':'123 Main St','city':'Atlanta','state':'GA','zipCode':'30301',
              'phone':post.fields['phone_primary'],'mobilePhone':post.fields['phone_other'],'email':'',
              'priceType':'Guaranteed','price':6}
        normalized=prepare_save_payload(data,post,expected)
        self.assertEqual(normalized['pricePeriod'],'Weekly');self.assertEqual(normalized['contactName'],'Salon')
        self.assertNotIn('pricePeriod',data)
        with self.assertRaises(NeedsHuman):prepare_save_payload({**data,'pricePeriod':'Monthly'},post,expected)
        with self.assertRaises(NeedsHuman):prepare_save_payload({**data,'price':999},post,expected)
        missing=raw_post();del missing['workingHours'];partial=posting_from_data(missing)
        normalized=prepare_save_payload(data,partial,partial.snapshot())
        self.assertNotIn('workingHours',normalized);self.assertIn('workingHours',partial.fields['_unknown_preserved'])

    def test_approval_only_parseable_server_publication_transition(self):
        post=posting_from_data(raw_post());expected=post.snapshot()
        actual=posting_from_data(raw_post(status='Approved',publishedAt='2026-10-12T10:00:00Z'))
        self.assertTrue(matches_expected(actual,expected,status='Approved'));self.assertFalse(matches_expected(actual,expected,status='Draft'))
        bad=posting_from_data(raw_post(status='Approved',publishedAt='garbage'))
        self.assertFalse(matches_expected(bad,expected,status='Approved'))
        changed=posting_from_data(raw_post(status='Approved',publishedAt='2026-10-12T10:00:00Z',isVIP=True))
        self.assertFalse(matches_expected(changed,expected,status='Approved'))

    def test_source_null_special_notes_empty_ui_restores_exact_null_only(self):
        post=posting_from_data(raw_post());expected=post.snapshot()
        data={'postingCategory':post.category,'status':post.status,'title':post.fields['title'],'content':post.content,
              'businessName':'Salon','address':'123 Main St','city':'Atlanta','state':'GA','zipCode':'30301',
              'phone':post.fields['phone_primary'],'mobilePhone':post.fields['phone_other'],'email':'',
              'priceType':'Guaranteed','price':6,'specialNotes':''}
        normalized=prepare_save_payload(data,post,expected)
        self.assertIsNone(normalized['specialNotes']);self.assertEqual(data['specialNotes'],'')
        for source_notes,ui_notes in [(None,'new notes'),('keep these notes',''),
                                      ('keep these notes',None),('',None)]:
            with self.subTest(source=source_notes,ui=ui_notes):
                source=posting_from_data(raw_post(specialNotes=source_notes))
                with self.assertRaises(NeedsHuman):
                    prepare_save_payload({**data,'specialNotes':ui_notes},source,source.snapshot())
        with self.assertRaises(NeedsHuman):
            prepare_save_payload({**data,'extraInformation':''},post,expected)

    def test_owner_sentinel_date_request_repair_audit_and_exact_readback(self):
        target='2026-10-12T00:00:00';sentinel='0001-01-01T00:00:00'
        with tempfile.TemporaryDirectory() as directory, \
                patch('tg_triage.nailhub.validation.processing_start_date',return_value=target), \
                patch('tg_triage.nailhub.browser.processing_start_date',return_value=target):
            post=posting_from_data(raw_post(startDate=sentinel))
            intended=proposal(post,extracted(post.content),settings(directory))
            expected={**post.snapshot(),'fields':intended['fields'],'audit':intended}
            data={'postingCategory':post.category,'status':post.status,'title':post.fields['title'],'content':post.content,
                  'businessName':'Salon','address':'123 Main St','city':'Atlanta','state':'GA','zipCode':'30301',
                  'phone':post.fields['phone_primary'],'mobilePhone':'','email':'',
                  'priceType':'Guaranteed','price':1200}
            for old_ui in ('omitted',None,sentinel,target):
                payload=dict(data)
                if old_ui!='omitted':payload['startDate']=old_ui
                normalized=prepare_save_payload(payload,post,expected)
                self.assertEqual(normalized['startDate'],target)
            with self.assertRaises(NeedsHuman):prepare_save_payload({**data,'startDate':'2030-01-01T00:00:00'},post,expected)
            with self.assertRaises(NeedsHuman):prepare_save_payload(data,post,{**expected,'audit':{}})
            actual=replace(post,fields=dict(intended['fields']))
            self.assertTrue(matches_expected(actual,expected,status='Draft'))
            preserved=json.loads(actual.fields['_preserved']);preserved['startDate']=sentinel
            actual.fields['_preserved']=json.dumps(preserved,sort_keys=True,ensure_ascii=False)
            self.assertFalse(matches_expected(actual,expected,status='Draft'))
            real=posting_from_data(raw_post(startDate='2020-01-01T00:00:00'))
            with self.assertRaises(NeedsHuman):prepare_save_payload(data,real,expected)

    def test_quill_paragraph_spacing_matches_api_html_but_changed_text_does_not(self):
        post=posting_from_data(raw_post());expected=post.snapshot()
        expected['content']=canonical_text('AI paragraph1\n\nAI paragraph2\n')
        actual=posting_from_data(raw_post(content='<p>AI paragraph1</p><p>AI paragraph2</p>'))
        self.assertTrue(matches_expected(actual,expected,status='Draft'))
        different=posting_from_data(raw_post(content='<p>AI paragraph1</p><p>Changed paragraph2</p>'))
        self.assertFalse(matches_expected(different,expected,status='Draft'))
        self.assertEqual(canonical_text('  Literal <p> text\n\n A & B  \n'),'Literal <p> text\nA & B')

class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self,directory,*,mode='preview',browser=None,model=None):
        cfg=settings(directory,mode=mode,verified=True,verification_ref='offline fake');post=posting_from_data(raw_post())
        browser=browser or FakeBrowser(post)
        model=model or SimpleNamespace(extract=Mock(return_value=extracted(post.content)),verify=Mock(return_value=True))
        service=Service(cfg,model=model,browser_factory=lambda _:browser);await service.scan();return service,browser,model

    async def test_preview_never_mutates_unchanged_not_new_job(self):
        with tempfile.TemporaryDirectory() as directory:
            service,browser,model=await self.exercise(directory);self.assertEqual(service.state.get(POST_ID)['state'],'preview')
            await service.scan();self.assertEqual(model.extract.call_count,1)
            self.assertNotIn('edit/AI',browser.calls);self.assertNotIn('save',browser.calls)

    async def test_live_saved_readback_before_approval_audit_persists(self):
        with tempfile.TemporaryDirectory() as directory:
            service,browser,_=await self.exercise(directory,mode='live')
            self.assertEqual(service.state.get(POST_ID)['state'],'approved');self.assertEqual(browser.calls[-3:],['edit/AI','save','approve'])
            record=json.loads(service.state.get(POST_ID)['expected'])
            self.assertIn('salary',record['audit']);self.assertIn('approval_publication',record['audit'])

    async def test_incomplete_address_or_zip_blocks_before_any_edit_ai_save_approve(self):
        for missing in ({'address':''},{'zipCode':''}):
            with tempfile.TemporaryDirectory() as directory:
                browser=FakeBrowser(posting_from_data(raw_post(**missing)))
                service,browser,model=await self.exercise(directory,mode='live',browser=browser)
                self.assertEqual(service.state.get(POST_ID)['state'],'needs-human')
                self.assertTrue(all(action not in browser.calls for action in ('edit/AI','save','approve')))
                model.verify.assert_not_called()

    async def test_save_timeout_or_mismatch_never_approves_retries(self):
        for values in ({'save_error':True},{'save_mismatch':True}):
            with tempfile.TemporaryDirectory() as directory:
                browser=FakeBrowser(posting_from_data(raw_post()),**values)
                service,browser,_=await self.exercise(directory,mode='live',browser=browser)
                self.assertEqual(service.state.get(POST_ID)['state'],'uncertain')
                await service.scan();self.assertEqual(browser.calls.count('save'),1);self.assertNotIn('approve',browser.calls)

    async def test_extraction_failure_held_before_edit_ai(self):
        with tempfile.TemporaryDirectory() as directory:
            model=SimpleNamespace(extract=Mock(side_effect=NeedsHuman('model failed')))
            service,browser,_=await self.exercise(directory,mode='live',model=model)
            self.assertEqual(service.state.get(POST_ID)['state'],'needs-human');await service.scan()
            self.assertEqual(model.extract.call_count,1);self.assertNotIn('edit/AI',browser.calls)
    async def test_owner_delegated_site_ai_output_saved_without_content_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            post=posting_from_data(raw_post())
            model=SimpleNamespace(extract=Mock(return_value=extracted(post.content)),
                verify=Mock(side_effect=AssertionError('site AI content checking is outside scope')))
            rewritten='Site AI changed amount to $9999/week and added free housing.'
            browser=FakeBrowser(post,rewrite=rewritten)
            original_edit=browser.edit_and_rewrite
            async def site_ai(post,changes):
                _,content=await original_edit(post,changes)
                return 'Site AI returned a new title',content
            browser.edit_and_rewrite=site_ai
            service,browser,_=await self.exercise(directory,mode='live',browser=browser,model=model)
            model.verify.assert_not_called();model.extract.assert_called_once_with(post)
            self.assertEqual(service.state.get(POST_ID)['state'],'approved')
            self.assertEqual(browser.calls[-3:],['edit/AI','save','approve'])
            expected=json.loads(service.state.get(POST_ID)['expected'])
            self.assertEqual(expected['content'],rewritten)
            self.assertEqual(expected['fields']['title'],'Site AI returned a new title')
            self.assertEqual(expected['fields']['salary_amount'],'1200.00')

    async def test_preview_to_live_reextracts(self):
        with tempfile.TemporaryDirectory() as directory:
            preview,browser,_=await self.exercise(directory)
            live=Service(replace(preview.config,mode='live'),model=preview.model,browser_factory=lambda _:browser)
            await live.scan();self.assertEqual(live.state.get(POST_ID)['state'],'approved');self.assertEqual(preview.model.extract.call_count,2)

    async def test_restart_boundaries_reconcile_without_mutation_replay(self):
        for state,status,wanted in [('before_save','Draft','needs-human'),('before_approve','Approved','approved')]:
            with tempfile.TemporaryDirectory() as directory:
                post=posting_from_data(raw_post());service=Service(settings(directory,mode='live'))
                service.state.observe(post.id,post.fingerprint,post.snapshot());service.state.mark(post.id,state,expected=post.snapshot())
                actual=posting_from_data(raw_post(status=status,publishedAt='2026-10-12T10:00:00Z' if status=='Approved' else None))
                browser=FakeBrowser(actual);await service.reconcile(browser)
                self.assertEqual(service.state.get(post.id)['state'],wanted);self.assertEqual(browser.calls,['read'])
        with tempfile.TemporaryDirectory() as directory:
            service=Service(settings(directory));post=posting_from_data(raw_post())
            service.state.observe(post.id,post.fingerprint,post.snapshot());service.state.mark(post.id,'before_save',expected=post.snapshot())
            await service.reconcile(FakeBrowser(replace(post,content='unknown remote')))
            self.assertEqual(service.state.get(post.id)['state'],'uncertain')

    async def test_pause_and_model_cancellation_finish_active_thread(self):
        with tempfile.TemporaryDirectory() as directory:
            service=Service(settings(directory));service.command('pause');self.assertTrue(service.state.status()['paused'])
            service.command('resume');self.assertFalse(service.state.status()['paused']);self.assertTrue(service.state.status()['pending'])
        started=threading.Event();release=threading.Event();done=threading.Event()
        def blocking():started.set();release.wait(2);done.set()
        task=asyncio.create_task(model_call(blocking));await asyncio.to_thread(started.wait,1)
        task.cancel();await asyncio.sleep(.01);self.assertFalse(task.done());release.set()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(done.is_set())
