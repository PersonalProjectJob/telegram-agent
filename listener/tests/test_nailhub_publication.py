"""Offline publication state-machine and UI protocol tests, not live website evidence."""
import copy
import json
import tempfile
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from tg_triage.nailhub.browser import Browser, posting_from_data, matches_expected, prepare_save_payload, listing_title, LIST_PATH
from tg_triage.nailhub.publication import (eligible, public_value, rewrite_audit,
    publication_expected, publication_valid, approval_readback)
from tg_triage.nailhub.service import Service
from tg_triage.nailhub.notices import manual_notice
from tg_triage.nailhub.validation import NeedsHuman, Posting
from tests.nailhub_fixtures import FakeBrowser, raw_post, extracted, settings

GUID='d01e44f5-38ab-413a-3861-08df28db55a0'


def approved(**values):
    return posting_from_data(raw_post(status='Approved',isPublic=False,
        publishedAt='2026-10-12T10:00:00',startDate='2026-10-12T00:00:00',**values))


def payload(original, expected):
    fields=expected['fields'];data=json.loads(fields['_preserved'])
    data.update(postingCategory=expected['category'],status=original.status,title=fields['title'],
        content=expected['content'],price=fields['salary_amount'] or None,
        priceType={'weekly':'Guaranteed','fixed':'Fixed','negotiated':'Negotiable','not_applicable':None}[fields['salary_type']])
    mapping={'salon':'businessName','address':'address','city':'city','state':'state','zip':'zipCode',
             'phone_primary':'phone','phone_other':'mobilePhone','email':'email'}
    data.update({remote:fields[key] for key,remote in mapping.items()})
    return data


def verified_audit(source, saved=None):
    saved=source if saved is None else saved
    return {'publication_workflow':rewrite_audit(source),
            'rewrite_save_verified':{'source_hash':source.fingerprint,'saved_hash':saved.fingerprint}}


class PublicationPolicyTests(unittest.TestCase):
    def test_inspected_job_metadata_preserved_without_inventing_missing_keys(self):
        values = {'numberOfOpening': 1, 'workSchedule': 'FullTime', 'skills': [],
                  'workingExperience': None, 'applyByDateTime': None}
        source = posting_from_data(raw_post(**values))
        expected = source.snapshot()
        stored = json.loads(source.fields['_preserved'])
        self.assertEqual({key: stored[key] for key in values}, values)
        submitted = payload(source, expected)
        for key in values:
            submitted.pop(key)
        restored = prepare_save_payload(submitted, source, expected)
        self.assertEqual({key: restored[key] for key in values}, values)
        for key, replacement in {'numberOfOpening': 2, 'workSchedule': 'PartTime',
                                 'skills': ['other'], 'workingExperience': '1 year',
                                 'applyByDateTime': '2026-11-01T00:00:00'}.items():
            with self.subTest(key=key), self.assertRaises(NeedsHuman):
                prepare_save_payload({**restored, key: replacement}, source, expected)
        absent = raw_post()
        for key in values:
            absent.pop(key, None)
        source_absent = posting_from_data(absent)
        absent_expected = source_absent.snapshot()
        result = prepare_save_payload(payload(source_absent, absent_expected), source_absent, absent_expected)
        self.assertTrue(all(key not in result for key in values))
        self.assertTrue(set(values).issubset(json.loads(source_absent.fields['_unknown_preserved'])))
        alternate = {'numberOfOpening': 3, 'workSchedule': 'PartTime',
                     'skills': ['acrylic', 'pedicure'], 'workingExperience': '2 years',
                     'applyByDateTime': '2026-11-01T10:30:00'}
        changed_post = posting_from_data(raw_post(**alternate))
        changed_expected = changed_post.snapshot()
        changed_payload = payload(changed_post, changed_expected)
        for key in alternate:
            changed_payload.pop(key)
        self.assertEqual({key: prepare_save_payload(changed_payload, changed_post, changed_expected)[key]
                          for key in alternate}, alternate)
        self.assertTrue(matches_expected(posting_from_data(raw_post(**alternate)), changed_expected, status='Draft'))
        self.assertFalse(matches_expected(posting_from_data(raw_post(**values)), changed_expected, status='Draft'))

    def test_known_false_approved_only_and_canonical_sequential_guid(self):
        source=approved(id=GUID);self.assertTrue(eligible(source));self.assertFalse(eligible(replace(source,status='Rejected')))
        for value in (True,None,0,'false'):
            raw=raw_post(status='Approved',isPublic=value)
            self.assertFalse(eligible(posting_from_data(raw)))
        raw=raw_post(status='Approved');raw.pop('isPublic')
        self.assertFalse(eligible(posting_from_data(raw)))
        notice=manual_notice({'id':GUID,'state':'needs-human','reason':'invalid evidence span',
                              'original':json.dumps(source.snapshot())})
        self.assertIn('https://nailhub.ai/admin/postings?id='+GUID,notice)
        for value in (None,0,'false'):
            self.assertFalse(eligible(posting_from_data(raw_post(isPublic=value))))
        self.assertTrue(eligible(posting_from_data(raw_post())))
        for invalid in (GUID+'/status',GUID+'?x=1','../'+GUID,GUID.replace('-','')):
            with self.assertRaises(NeedsHuman):posting_from_data(raw_post(id=invalid))

    def test_rewrite_save_cannot_publish_and_publication_cannot_rewrite(self):
        source=approved();expected={**source.snapshot(),'audit':{'publication_workflow':rewrite_audit(source)}}
        data=payload(source,expected);normalized=prepare_save_payload(data,source,expected)
        self.assertFalse(normalized['isPublic']);self.assertIsNone(normalized['startDate'])
        with self.assertRaises(NeedsHuman):prepare_save_payload({**data,'isPublic':True},source,expected)
        with self.assertRaises(NeedsHuman):prepare_save_payload({**data,'startDate':'2026-10-14T00:00:00'},source,expected)
        with self.assertRaises(NeedsHuman):publication_expected(source)
        final=publication_expected(source,audit=verified_audit(source));self.assertTrue(publication_valid(source,final))
        normalized=prepare_save_payload(payload(source,final),source,final)
        self.assertTrue(normalized['isPublic']);self.assertIsNone(normalized['startDate'])
        for key,value in [('content','Another body'),('category','Portfolio')]:
            changed=copy.deepcopy(final);changed[key]=value
            self.assertFalse(publication_valid(source,changed))
            with self.assertRaises(NeedsHuman):prepare_save_payload(payload(source,changed),source,changed)
        changed=copy.deepcopy(final);changed['fields']['title']='Another title'
        self.assertFalse(publication_valid(source,changed))
        forged=copy.deepcopy(final);forged['audit']['publication_transition']['source_hash']='forged'
        self.assertFalse(publication_valid(source,forged))
        for audit in ({}, {'publication_workflow':rewrite_audit(source)},
                      {**verified_audit(source),'rewrite_save_verified':{'source_hash':source.fingerprint,'saved_hash':'forged'}}):
            with self.assertRaises(NeedsHuman):publication_expected(source,audit=audit)

    def test_approve_expects_public_true_but_publish_keeps_exact_publication_date(self):
        draft=posting_from_data(raw_post());final=publication_expected(draft,approve=True)
        self.assertTrue(approval_readback(final))
        actual=posting_from_data(raw_post(status='Approved',isPublic=True,publishedAt='2026-10-13T10:00:00'))
        self.assertTrue(matches_expected(actual,final,status='Approved'))
        self.assertFalse(matches_expected(replace(actual,fields=draft.fields),final,status='Approved'))
        source=approved();expected=publication_expected(source,audit=verified_audit(source))
        actual=Posting(source.id,'Approved',source.category,source.content,expected['fields'])
        self.assertTrue(matches_expected(actual,expected,status='Approved'))
        metadata=json.loads(actual.fields['_preserved']);metadata['publishedAt']='2026-10-13T10:00:00'
        fields={**actual.fields,'_preserved':json.dumps(metadata,sort_keys=True,ensure_ascii=False)}
        self.assertFalse(matches_expected(replace(actual,fields=fields),expected,status='Approved'))


class PublicationServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_approved_false_full_rules_ai_save_false_then_checkbox_save_true(self):
        with tempfile.TemporaryDirectory() as directory:
            source=approved();browser=FakeBrowser(source,rewrite='Site AI output')
            model=SimpleNamespace(extract=Mock(return_value=extracted(source.content)),verify=Mock(side_effect=AssertionError('unused')))
            service=Service(settings(directory,mode='live',verified=True,verification_ref='offline fake'),model=model,browser_factory=lambda _:browser)
            async def publish(saved,expected):
                self.assertEqual(service.state.get(source.id)['state'],'before_publish')
                self.assertFalse(public_value(saved));self.assertEqual(saved.content,'Site AI output')
                browser.calls.append('publish/Save')
                browser.post=Posting(saved.id,'Approved',saved.category,saved.content,dict(expected['fields']))
                return browser.post
            browser.publish=AsyncMock(side_effect=publish)
            await service.scan()
            self.assertEqual(browser.calls[-3:],['edit/AI','save','publish/Save'])
            self.assertTrue(public_value(browser.post));self.assertNotIn('approve',browser.calls)
            self.assertEqual(service.state.get(source.id)['state'],'approved')
            audit=json.loads(service.state.get(source.id)['expected'])['audit']
            self.assertIn('publication_transition',audit);self.assertIn('publication_workflow',audit)
            await service.scan();self.assertEqual(browser.calls.count('edit/AI'),1);model.verify.assert_not_called()

    async def test_recovery_requires_final_publication_not_approved_status_alone(self):
        for checkpoint,visible,wanted in [('before_save',False,'uncertain'),('before_publish',False,'uncertain'),
                                          ('before_publish',True,'approved')]:
            with self.subTest(checkpoint=checkpoint,visible=visible),tempfile.TemporaryDirectory() as directory:
                source=approved();service=Service(settings(directory));state=service.state
                state.observe(source.id,source.fingerprint,source.snapshot())
                expected=(publication_expected(source,audit=verified_audit(source)) if checkpoint=='before_publish' else
                          {**source.snapshot(),'audit':{'publication_workflow':rewrite_audit(source)}})
                state.mark(source.id,checkpoint,expected=expected)
                actual=Posting(source.id,'Approved',source.category,source.content,
                    dict(expected['fields']) if visible else source.fields)
                browser=FakeBrowser(actual);await service.reconcile(browser)
                self.assertEqual(state.get(source.id)['state'],wanted);self.assertEqual(browser.calls,['read'])
                self.assertFalse(state.observe(source.id,'changed',source.snapshot()))

    async def test_publish_timeout_locks_without_replaying_site_mutations(self):
        with tempfile.TemporaryDirectory() as directory:
            source=approved();browser=FakeBrowser(source);browser.publish=AsyncMock(side_effect=TimeoutError())
            model=SimpleNamespace(extract=Mock(return_value=extracted(source.content)))
            service=Service(settings(directory,mode='live',verified=True,verification_ref='offline fake'),model=model,browser_factory=lambda _:browser)
            await service.scan();self.assertEqual(service.state.get(source.id)['state'],'uncertain')
            await service.scan();browser.publish.assert_awaited_once();self.assertEqual(browser.calls.count('save'),1)

    async def test_false_legacy_final_checkpoints_never_mark_completed(self):
        for checkpoint in ('before_approve','before_publish'):
            with self.subTest(checkpoint=checkpoint),tempfile.TemporaryDirectory() as directory:
                source=approved();service=Service(settings(directory));state=service.state
                state.observe(source.id,source.fingerprint,source.snapshot())
                state.mark(source.id,checkpoint,expected=source.snapshot())
                browser=FakeBrowser(source);await service.reconcile(browser)
                self.assertEqual(state.get(source.id)['state'],'uncertain');self.assertEqual(browser.calls,['read'])

    async def test_unknown_visibility_holds_before_model_or_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            source=posting_from_data(raw_post(isPublic=None));service=Service(settings(directory),model=Mock())
            service.state.observe(source.id,source.fingerprint,source.snapshot());browser=FakeBrowser(source)
            await service.process(browser,source)
            row=service.state.get(source.id);self.assertEqual(row['state'],'needs-human')
            self.assertEqual(json.loads(row['details'])['field'],'isPublic')
            service.model.extract.assert_not_called();self.assertEqual(browser.calls,[])

    async def test_actual_checkbox_contract_and_no_ai_or_approve_during_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            source=approved();expected=publication_expected(source,audit=verified_audit(source))
            browser=Browser(settings(directory,mode='live',verified=True,verification_ref='offline fake'),page=Mock())
            browser.verify_listing_age=AsyncMock()
            browser._open_edit=AsyncMock();browser.control=AsyncMock(return_value=SimpleNamespace(inner_text=AsyncMock(return_value=source.content)))
            checkbox=SimpleNamespace(get_attribute=AsyncMock(side_effect=['false','true']),click=AsyncMock())
            browser.one=AsyncMock(return_value=checkbox)
            browser._field=AsyncMock(return_value=SimpleNamespace(input_value=AsyncMock(return_value=source.fields['title'])))
            browser.save=AsyncMock(return_value=source)
            await browser.publish(source,expected)
            browser.one.assert_awaited_once_with('[role=dialog] #isPublic');checkbox.click.assert_awaited_once()
            browser.save.assert_awaited_once_with(source,expected)
            with self.assertRaises(NeedsHuman):await browser.approve(source)

    async def test_listing_and_read_accept_actual_sequential_guid_not_public_approved(self):
        with tempfile.TemporaryDirectory() as directory:
            browser=Browser(settings(directory),page=Mock());browser.ready=AsyncMock()
            source=approved(id=GUID);raw=raw_post(id=GUID,status='Approved',isPublic=False)
            browser._navigate=AsyncMock(return_value={'items':[raw,raw_post(status='Approved',isPublic=True)],'pageNumber':1,'hasNextPage':False})
            browser._verified_listing_rows=AsyncMock(return_value=[
                (posting_from_data(raw),None,{'id':GUID}),
                (posting_from_data(raw_post(status='Approved',isPublic=True)),None,{'id':raw_post()['id']})])
            results=await browser.candidates();self.assertEqual([item.id for item in results],[GUID])
            browser._navigate=AsyncMock(return_value=raw)
            async def control(key):
                return SimpleNamespace(wait_for=AsyncMock(),inner_text=AsyncMock(return_value=source.content if key=='detail_content' else source.fields['title']))
            browser.control=control
            browser._read_via_listing=AsyncMock(return_value=raw)
            self.assertEqual((await browser.read(GUID)).id,GUID)
            browser._read_via_listing.assert_awaited_once_with(GUID)


class SequentialGuidListingTests(unittest.IsolatedAsyncioTestCase):
    async def test_save_response_bound_includes_secondary_listing_freshness_read(self):
        import asyncio
        class Response:
            async def __aenter__(self):
                self.value=asyncio.get_running_loop().create_future()
                self.value.set_result(SimpleNamespace(status=200));return self
            async def __aexit__(self,*args):pass
        with tempfile.TemporaryDirectory() as directory:
            source=approved(id=GUID)
            page=SimpleNamespace(route=AsyncMock(),unroute=AsyncMock(),expect_response=Mock(return_value=Response()))
            browser=Browser(settings(directory,mode='live',verified=True,verification_ref='offline fake',timeout_seconds=600),page=page)
            browser.authenticated=AsyncMock();browser.assert_fresh=AsyncMock();browser.action_click=AsyncMock()
            browser.verify_listing_age=AsyncMock()
            browser.read=AsyncMock(return_value=source)
            expected={**source.snapshot(),'audit':{'publication_workflow':rewrite_audit(source)}}
            await browser.save(source,expected)
            self.assertEqual(page.expect_response.call_args.kwargs['timeout'],600000)
            browser.action_click.assert_awaited_once_with('save')
            browser.read.assert_awaited_once_with(GUID)

    async def exercise(self, directory, *, failure=None, layout=10):
        import asyncio
        target=raw_post(id=GUID,title='😀'*11,businessName='Zaza',status='Approved',isPublic=False,
                        created='2026-10-14T00:51:05.188928')
        other=raw_post(title='Other posting',businessName='Other salon',
                       created='2026-10-14T00:51:05.188928')
        pages=[{'items':[other],'pageNumber':1,'hasNextPage':True},
               {'items':[target],'pageNumber':2,'hasNextPage':False}]
        if failure=='duplicate':pages[1]['items']=[other,target]
        if failure=='missing':pages[1]['items']=[{**other,'id':'22222222-2222-4222-8222-222222222222'}]
        position=[0];buttons=[]
        class Response:
            async def __aenter__(self):
                self.value=asyncio.get_running_loop().create_future();self.value.set_result(object());return self
            async def __aexit__(self,*args):pass
        page=SimpleNamespace(expect_response=Mock(side_effect=lambda *args,**kw:Response()),wait_for_load_state=AsyncMock())
        browser=Browser(settings(directory,max_pages=1 if failure=='bound' else 5),page=page)
        browser._navigate=AsyncMock(return_value=pages[0]);browser.authenticated=AsyncMock()
        detail={**target,'id':other['id']} if failure=='detailID' else target
        browser._json=AsyncMock(side_effect=[pages[1],detail])
        def row(raw):
            cells=[listing_title(raw['title']),raw['businessName'],'-',raw['state'],raw['city'],raw['zipCode'],raw['postingCategory'],raw['status'],'10/14/2026','View']
            if layout == 12:
                cells[7:7] = ['-', '-']
            if failure=='shifted':cells[1]='Wrong salon'
            if failure=='status':cells[9 if layout == 12 else 7]='Wrong status'
            button=SimpleNamespace(wait_for=AsyncMock(),count=AsyncMock(return_value=2 if failure=='button' else 1),
                get_attribute=AsyncMock(return_value='View Posting Detail'),click=AsyncMock())
            buttons.append(button)
            return SimpleNamespace(wait_for=AsyncMock(),
                                   locator=Mock(return_value=SimpleNamespace(all_inner_texts=AsyncMock(return_value=cells))),
                                   get_by_role=Mock(return_value=button))
        async def one(selector):
            self.assertEqual(selector,'table')
            rows=[row(raw) for raw in pages[position[0]]['items']]
            headers=['Title','Business Name','Posted By','State','City','Zip Code','Category','Status','Created','Actions']
            if layout == 12:
                headers[7:7] = ['Schedule', 'Apply By']
            if failure=='headers':headers[0]='Unknown'
            def locator(selector):
                if selector=='thead th':return SimpleNamespace(all_inner_texts=AsyncMock(return_value=headers))
                self.assertEqual(selector,'tbody > tr')
                return SimpleNamespace(count=AsyncMock(return_value=len(rows)+1 if failure=='rows' else len(rows)),nth=lambda index:rows[index])
            return SimpleNamespace(locator=locator)
        browser.one=one
        async def next_click():position[0]+=1
        next_button=SimpleNamespace(is_enabled=AsyncMock(return_value=True),click=AsyncMock(side_effect=next_click))
        async def control(key):
            if key=='next_page':return next_button
            value=target['content'] if key=='detail_content' else target['title']
            return SimpleNamespace(wait_for=AsyncMock(),inner_text=AsyncMock(return_value=value))
        browser.control=control
        if failure:
            with self.assertRaises(NeedsHuman):await browser.read(GUID)
            if failure!='detailID':self.assertTrue(all(button.click.await_count==0 for button in buttons))
        else:
            actual=await browser.read(GUID);self.assertEqual(actual.id,GUID)
            browser._navigate.assert_awaited_once_with(browser.config.url,LIST_PATH)
            next_button.click.assert_awaited_once()
            self.assertEqual(sum(button.click.await_count for button in buttons),1)
            self.assertEqual(listing_title(target['title']),'😀'*10+'...')

    async def test_exact_id_row_mapping_pagination_and_utf16_title(self):
        with tempfile.TemporaryDirectory() as directory:await self.exercise(directory)
        with tempfile.TemporaryDirectory() as directory:await self.exercise(directory,layout=12)
        self.assertEqual(listing_title('a'*20),'a'*20)
        self.assertEqual(listing_title('a'*21),'a'*20+'...')
        self.assertEqual(len(listing_title('a'+'😀'*10)[:-3].encode('utf-16-le',errors='surrogatepass')),40)

    async def test_shifted_missing_duplicate_rows_and_wrong_detail_hold(self):
        for failure in ('shifted','missing','duplicate','rows','headers','status','button','detailID','bound'):
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as directory:
                await self.exercise(directory,failure=failure)
                await self.exercise(directory,failure=failure,layout=12)

    async def test_rfc_guid_uses_original_deep_link_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            raw=raw_post();browser=Browser(settings(directory),page=Mock());browser._navigate=AsyncMock(return_value=raw)
            browser._read_via_listing=AsyncMock(side_effect=AssertionError('unexpected fallback'))
            async def control(key):
                return SimpleNamespace(wait_for=AsyncMock(),inner_text=AsyncMock(return_value=raw['content'] if key=='detail_content' else raw['title']))
            browser.control=control
            await browser.read(raw['id']);browser._read_via_listing.assert_not_awaited()
            browser._navigate.assert_awaited_once_with(browser.config.url+'?id='+raw['id'],LIST_PATH+'/'+raw['id'])
