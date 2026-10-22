"""Security, durable-state and lifecycle regression tests. No network or paid provider."""
import copy
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from nailblog.admin_security import Guard, load_credentials
from nailblog.admin_state import AdminStore
from nailblog.config import AdminConfig, ContractConfig, STAGING_API_ORIGIN, STAGING_SITE_ORIGIN
from nailblog.publication import AdminError, matches, same_html, snapshot
from nailblog.schedule import VN_TZ
from nailblog.service import Service
from nailblog.state import State
from tests.helpers import settings, load

POST_ID = '12345678-1234-1234-1234-123456789abc'
CATEGORY = '87654321-1234-1234-1234-123456789abc'
SLOT = datetime(2026, 10, 22, 6, tzinfo=VN_TZ)


def config(directory, mode='live'):
    return settings(directory, mode=mode, contract=ContractConfig(
        STAGING_API_ORIGIN, STAGING_SITE_ORIGIN, True, str(Path(directory)/'report.json'), 'staging'),
        admin=AdminConfig(str(Path(directory)/'credentials.json'), str(Path(directory)/'browser')))


def payload():
    def media(n):
        return {'imageUrl': STAGING_API_ORIGIN+f'/images/{n}.webp', 'thumbnailUrl': STAGING_API_ORIGIN+f'/images/t{n}.webp',
                'imageRelativePath': f'images/{n}.webp', 'thumbnailRelativePath': f'images/t{n}.webp'}
    images = [media(n) for n in range(1, 4)]
    content = '<h2>Mẫu móng tay mùa thu</h2><p>Nội dung tiếng Việt đã duyệt.</p>'
    content += ''.join(f'<figure><img src="{x["imageUrl"]}" alt="Mẫu móng tay minh họa cho mùa thu"><figcaption>Ảnh minh họa</figcaption></figure>' for x in images)
    return {'categoryId': CATEGORY, 'status': 'Draft', 'publishedAt': SLOT.isoformat(), 'isPublic': True,
            'isFeatured': False, 'coverImage': media(0), 'images': images, 'hashtags': ['nails','mua-thu','nailhub'],
            'translations': [{'languageCode':'VI','title':'Mẫu móng tay mùa thu', 'summary':'Tóm tắt bài viết.',
                              'content':content,'metaTitle':'Mẫu móng tay mùa thu','metaDescription':'Mô tả bài viết.'}]}


def document(p=None):
    p=p or payload();vi=p['translations'][0]
    hashes=[str(x)*64 for x in range(1,5)]
    return {'admin_category_id':p['categoryId'],'title':vi['title'],'summary':vi['summary'],'meta_title':vi['metaTitle'],
            'meta_description':vi['metaDescription'],'content_html':vi['content'],'hashtags':p['hashtags'],
            'quality':{'verdict':'pass'},'image_hashes':hashes,
            'image_reviews':{h:dict.fromkeys(('technical','anatomy','topic','brand','distinct'),True) for h in hashes}}


class ConfigAndPayloadTests(unittest.TestCase):
    def test_staging_defaults_and_cta_follow_environment(self):
        with tempfile.TemporaryDirectory() as d:
            c = load(d, contract='environment="staging"')
            self.assertEqual((c.contract.api_origin, c.contract.site_origin), (STAGING_API_ORIGIN, STAGING_SITE_ORIGIN))
            self.assertEqual(c.links.jobs, STAGING_SITE_ORIGIN+'/')

    def test_staging_rejects_production_api_and_links(self):
        with tempfile.TemporaryDirectory() as d:
            for sections in ({'contract':'environment="staging"\napi_origin="https://api.nailhub.ai"'},
                             {'contract':'environment="staging"','links':'app="https://nailhub.ai/"'}):
                with self.assertRaises(ValueError): load(d, **sections)

    def test_credential_schema_empty_symlink_and_redacted_error(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'c.json';p.write_text('{"email":"dummy@example.test","password":"fixture-secret"}')
            p.chmod(0o600)
            self.assertEqual(set(load_credentials(p)), {'email','password'})
            p.write_text('{"email":"","password":"fixture-secret"}')
            with self.assertRaisesRegex(AdminError, '^credential_file_invalid$'): load_credentials(p)

    def test_valid_snapshot_and_server_media_ids(self):
        with tempfile.TemporaryDirectory() as d:
            a=payload();b=copy.deepcopy(a);b['coverImage']['id']=POST_ID;b['id']=POST_ID
            self.assertTrue(matches(b,a,config(d),post_id=POST_ID))

    def test_real_images_alt_and_visibility_required(self):
        with tempfile.TemporaryDirectory() as d:
            for mutate in (lambda p:p.update(images=[]),lambda p:p.update(isPublic=False),
                           lambda p:p['translations'][0].update(content='<p>placeholder</p>'),
                           lambda p:p['images'][0].update(imageUrl='https://api.nailhub.ai/image.webp')):
                p=payload();mutate(p)
                with self.assertRaises(AdminError):snapshot(p,config(d))

    def test_editor_loss_and_markup_attack_detected(self):
        self.assertFalse(same_html('<figure><p>caption</p></figure>','<p>caption</p>'))
        for content in ('<script>x</script>','<img src="javascript:x">','<p onclick="x">x</p>'):
            with self.assertRaises(AdminError):same_html(content,content)

    def test_readback_wrong_identity_status_content_and_time(self):
        with tempfile.TemporaryDirectory() as d:
            c=config(d);p=payload()
            for change in ({'id':'wrong'},{'status':'Published'},{'categoryId':POST_ID}):
                self.assertFalse(matches({**p,'id':POST_ID,**change},p,c,post_id=POST_ID))
            a=copy.deepcopy(p);a['translations'][0]['content']+='<p>extra</p>'
            self.assertFalse(matches(a,p,c))
            a={**p,'status':'Published','publishedAt':(SLOT+timedelta(minutes=16)).isoformat()}
            self.assertFalse(matches(a,p,c,status='Published',published=True))


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.c=config(self.tmp.name);self.g=Guard(self.c)

    def request(self, method='POST', path='/api/v1/admin/blogs', origin=STAGING_API_ORIGIN, data=None, redirected=None):
        return SimpleNamespace(url=origin+path,method=method,post_data_json=data,redirected_from=redirected,headers={})

    def test_default_deny_and_preview_no_non_get(self):
        for method in ('POST','PUT','PATCH','DELETE','HEAD','OPTIONS'):
            self.assertFalse(Guard(config(self.tmp.name,'preview')).permits(self.request(method)))
        self.assertFalse(self.g.permits(self.request()))

    def test_capability_exact_origin_method_path_payload_once(self):
        with self.g.allow('POST','/api/v1/admin/blogs',lambda q:q.post_data_json=={'status':'Draft'}):
            for req in (self.request(origin='https://api.nailhub.ai'),self.request(method='PUT'),
                        self.request(path='/api/v1/admin/blogs/other'),self.request(data={'status':'Published'}),
                        self.request(data={'status':'Draft'},redirected=object())):
                self.assertFalse(self.g.permits(req))
            self.assertTrue(self.g.permits(self.request(data={'status':'Draft'})))
            self.assertFalse(self.g.permits(self.request(data={'status':'Draft'})))
        self.assertFalse(self.g.permits(self.request(data={'status':'Draft'})))

    def test_probe_only_allows_login_and_never_production(self):
        g=Guard(replace(self.c,mode='probe'))
        with self.assertRaises(AdminError):
            with g.allow('POST','/api/v1/admin/blogs',lambda q:True):pass
        with g.allow('POST','/api/v1/authentication/signin',lambda q:True,login=True):
            self.assertFalse(g.permits(self.request(path='/api/v1/authentication/signin',origin='https://api.nailhub.ai')))
            self.assertTrue(g.permits(self.request(path='/api/v1/authentication/signin')))


class DurableTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.c=config(self.tmp.name);self.s=State(self.c.db_path);self.store=AdminStore(self.s,self.c)
        self.p=payload();self.post=self.s.add_post(SLOT.isoformat(),draft=document())

    def draft(self):
        self.store.claim_create(self.post,self.p)
        self.store.record_draft(self.post,{**self.p,'id':POST_ID})

    def approved(self):
        self.draft();self.store.approve(self.post,(SLOT-timedelta(hours=3)).timestamp())

    def test_unknown_create_outcome_is_durable_and_not_retried(self):
        self.store.claim_create(self.post,self.p)
        with self.assertRaisesRegex(AdminError,'reconciliation'):
            AdminStore(State(self.c.db_path),self.c).claim_create(self.post,self.p)

    def test_record_draft_and_approve_require_verified_readback(self):
        with self.assertRaises(AdminError):self.store.approve(self.post,0)
        self.store.claim_create(self.post,self.p)
        with self.assertRaises(AdminError):self.store.record_draft(self.post,{**self.p,'id':POST_ID,'status':'Published'})
        self.store.record_draft(self.post,{**self.p,'id':POST_ID})
        self.assertEqual(self.s.get_post(self.post)['state'],'review')
        self.store.approve(self.post,(SLOT-timedelta(hours=3)).timestamp())
        self.assertEqual(self.s.get_post(self.post)['state'],'scheduled')

    def test_late_approval_fails(self):
        self.draft()
        with self.assertRaisesRegex(AdminError,'deadline'):self.store.approve(self.post,SLOT.timestamp())

    def test_unrelated_payload_cannot_borrow_quality_from_local_document(self):
        bad=payload();bad['translations'][0]['title']='Bài hoàn toàn khác với bản Owner xem'
        with self.assertRaisesRegex(AdminError,'document_payload_mismatch'):
            self.store.claim_create(self.post,bad)

    def test_move_before_approval_requires_new_remote_draft(self):
        self.draft();self.s.move_post(self.post,(SLOT+timedelta(days=1)).isoformat())
        with self.assertRaisesRegex(AdminError,'slot_payload_mismatch'):
            self.store.approve(self.post,(SLOT+timedelta(hours=3)).timestamp())

    def test_wrong_category_rejected_even_with_identical_content(self):
        p=payload();p['categoryId']=POST_ID
        with self.assertRaisesRegex(AdminError,'category_mismatch'):self.store.claim_create(self.post,p)

    def test_pause_after_claim_stops_actual_publish_request(self):
        self.approved();self.store.claim_publish(self.post,SLOT.timestamp())
        self.s.set_paused(True)
        with self.assertRaisesRegex(AdminError,'publish_write_no_longer_allowed'):
            self.store.check_publish_write(self.post,SLOT.timestamp())

    def test_materialize_only_updates_uploaded_urls_and_binds_receipt(self):
        self.store.claim_create(self.post,self.p)
        new=copy.deepcopy(self.p)
        for img in [new['coverImage'],*new['images']]:
            img['imageUrl']=img['imageUrl'].replace('/images/','/uploaded/')
        new['translations'][0]['content']=new['translations'][0]['content'].replace('/images/','/uploaded/')
        self.store.materialized(self.post,new)
        self.assertIn('/uploaded/',json.loads(self.s.get_post(self.post)['draft_json'])['content_html'])
        self.store.record_draft(self.post,{**new,'id':POST_ID})
        self.store.approve(self.post,(SLOT-timedelta(hours=3)).timestamp())
        with self.s.connection() as db:
            tampered=copy.deepcopy(new);tampered['translations'][0]['summary']='Tampered'
            db.execute('UPDATE admin_operations SET payload=? WHERE post_id=?',(json.dumps(tampered),self.post))
        with self.assertRaisesRegex(AdminError,'approval_changed'):
            self.store.claim_publish(self.post,SLOT.timestamp())

    def test_publish_claim_atomic_and_checkpoint_survives_details(self):
        self.approved();self.store.claim_publish(self.post,SLOT.timestamp())
        self.assertEqual(State(self.c.db_path).get_post(self.post)['state'],'before_publish')
        with self.assertRaises(AdminError):AdminStore(State(self.c.db_path),self.c).claim_publish(self.post,SLOT.timestamp())
        self.s.transition(self.post,'uncertain','network_timeout')
        self.assertTrue(self.store.operation(self.post)['approval'])

    def test_edited_document_or_moved_slot_invalidates_approval(self):
        self.approved();self.s.move_post(self.post,(SLOT+timedelta(hours=1)).isoformat())
        with self.assertRaisesRegex(AdminError,'approval_changed'):self.store.claim_publish(self.post,(SLOT+timedelta(hours=1)).timestamp())

    def test_pause_and_late_window_block_claim(self):
        self.approved();self.s.set_paused(True)
        with self.assertRaisesRegex(AdminError,'paused'):self.store.claim_publish(self.post,SLOT.timestamp())
        self.s.set_paused(False)
        for minutes in (-1,16):
            with self.assertRaisesRegex(AdminError,'window'):self.store.claim_publish(self.post,(SLOT+timedelta(minutes=minutes)).timestamp())

    def test_state_environment_rebinding_rejected(self):
        with self.s.connection() as db:db.execute("UPDATE admin_binding SET value='{}'")
        with self.assertRaisesRegex(AdminError,'environment'):AdminStore(self.s,self.c)

    def test_one_publish_per_day_even_for_other_slot(self):
        self.approved();self.store.claim_publish(self.post,SLOT.timestamp())
        other=self.s.add_post((SLOT+timedelta(hours=13)).isoformat(),draft=document())
        p=payload();p['publishedAt']=(SLOT+timedelta(hours=13)).isoformat()
        self.store.claim_create(other,p);self.store.record_draft(other,{**p,'id':CATEGORY})
        self.store.approve(other,(SLOT+timedelta(hours=9)).timestamp())
        with self.assertRaisesRegex(AdminError,'daily_publish_limit'):
            self.store.claim_publish(other,(SLOT+timedelta(hours=13)).timestamp())


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_failure_then_restart_recovers_once_without_recreating(self):
        with tempfile.TemporaryDirectory() as d:
            c=config(d);service=Service(c);p=payload()
            n=service.state.add_post(SLOT.isoformat(),draft=document())
            uploaded=copy.deepcopy(p)
            for image in [uploaded['coverImage'],*uploaded['images']]:
                image['imageUrl']=image['imageUrl'].replace('/images/','/uploaded/')
            uploaded['translations'][0]['content']=uploaded['translations'][0]['content'].replace('/images/','/uploaded/')
            remote={**uploaded,'id':POST_ID}
            async def uncertain_create(value,before_create,**kwargs):
                before_create(uploaded)
                raise TimeoutError()
            fake=SimpleNamespace(ready=AsyncMock(),guard=SimpleNamespace(),
                save_draft=AsyncMock(side_effect=uncertain_create),upload=AsyncMock(),
                find_drafts=AsyncMock(return_value=[remote]),read=AsyncMock(return_value=remote))
            context=AsyncMock();context.__aenter__.return_value=fake
            context.write_ready=lambda:None
            hashes=document()['image_hashes']
            with patch('nailblog.admin.Admin',return_value=context), \
                    patch('nailblog.admin_images.validate_files',return_value=hashes):
                with self.assertRaises(TimeoutError):
                    await service.save_remote_draft(n,p,cover_path='cover',image_paths=['1','2','3'])
                self.assertIsNotNone(AdminStore(service.state,c).operation(n))
                restarted=Service(c,state=State(c.db_path))
                self.assertEqual(await restarted.save_remote_draft(n,p,cover_path='missing',image_paths=[]),remote)
                self.assertEqual(restarted.state.get_post(n)['admin_id'],POST_ID)
                await Service(c,state=State(c.db_path)).save_remote_draft(n,p,cover_path='missing',image_paths=[])
            fake.save_draft.assert_awaited_once();fake.upload.assert_not_awaited()
            fake.find_drafts.assert_awaited_once_with(uploaded);fake.read.assert_awaited_once_with(POST_ID)

    async def test_unresolved_create_recovery_never_uploads_or_creates_again(self):
        for count in (0,2):
            with self.subTest(matches=count), tempfile.TemporaryDirectory() as d:
                c=config(d);s=State(c.db_path);p=payload()
                n=s.add_post(SLOT.isoformat(),draft=document());AdminStore(s,c).claim_create(n,p)
                remote={**p,'id':POST_ID}
                fake=SimpleNamespace(ready=AsyncMock(),guard=SimpleNamespace(),save_draft=AsyncMock(),
                    upload=AsyncMock(),find_drafts=AsyncMock(return_value=[remote]*count),read=AsyncMock())
                context=AsyncMock();context.__aenter__.return_value=fake;context.write_ready=lambda:None
                with patch('nailblog.admin.Admin',return_value=context), \
                        patch('nailblog.admin_images.validate_files') as validate:
                    for _ in range(2):
                        with self.assertRaisesRegex(AdminError,'manual_reconciliation'):
                            await Service(c,state=State(c.db_path)).save_remote_draft(n,p,cover_path='missing',image_paths=[])
                fake.save_draft.assert_not_awaited();fake.upload.assert_not_awaited();fake.read.assert_not_awaited()
                validate.assert_not_called();self.assertEqual(fake.find_drafts.await_count,2)
                self.assertFalse(s.get_post(n)['admin_id'])

    async def test_preview_never_instantiates_admin(self):
        with tempfile.TemporaryDirectory() as d, patch('nailblog.admin.Admin') as admin:
            self.assertEqual(await Service(config(d,'preview')).publish_pass(),[])
            admin.assert_not_called()

    async def test_before_publish_recovery_reads_only_and_stays_uncertain(self):
        with tempfile.TemporaryDirectory() as d:
            c=config(d);s=State(c.db_path);store=AdminStore(s,c);p=payload()
            n=s.add_post(SLOT.isoformat(),draft=document())
            store.claim_create(n,p);store.record_draft(n,{**p,'id':POST_ID})
            store.approve(n,(SLOT-timedelta(hours=3)).timestamp());store.claim_publish(n,SLOT.timestamp())
            fake=SimpleNamespace(ready=AsyncMock(),guard=SimpleNamespace(),publish=AsyncMock(),published_readback=AsyncMock(side_effect=AdminError('unconfirmed')))
            context=AsyncMock();context.__aenter__.return_value=fake
            with patch('nailblog.admin.Admin',return_value=context):
                service=Service(c,state=s,clock=lambda:SLOT.timestamp())
                await service.publish_pass();await service.publish_pass()
            fake.publish.assert_not_called();self.assertEqual(fake.published_readback.await_count,2)
            self.assertEqual(s.get_post(n)['state'],'uncertain')

    async def test_editor_block_precedes_any_create_intent_or_browser_launch(self):
        with tempfile.TemporaryDirectory() as d:
            c=config(d);p=Path(c.contract.verification_ref)
            p.write_text(json.dumps({'environment':'staging','site_origin':STAGING_SITE_ORIGIN,
                                     'api_origin':STAGING_API_ORIGIN,'authenticated':True,'editor_roundtrip':False}))
            service=Service(c)
            n=service.state.add_post(SLOT.isoformat(),draft=document())
            with self.assertRaisesRegex(AdminError,'editor_loss'):
                await service.save_remote_draft(n,payload(),cover_path='none',image_paths=[])
            self.assertIsNone(AdminStore(service.state,c).operation(n))


class ImageTests(unittest.TestCase):
    def test_renamed_bytes_and_wrong_dimensions_rejected(self):
        from nailblog.admin_images import validate_files
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'fake.webp';p.write_bytes(b'not an image')
            with self.assertRaisesRegex(AdminError,'image_bytes'):validate_files(p,[p,p,p])
            Image.new('RGB',(20,20),'blue').save(p,format='WEBP')
            with self.assertRaisesRegex(AdminError,'image_bytes'):validate_files(p,[p,p,p])

    def test_duplicate_pixels_rejected_even_with_distinct_files(self):
        from nailblog.admin_images import validate_files
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            cover=Path(d)/'cover.webp';Image.new('RGB',(1600,900),'blue').save(cover,format='WEBP')
            paths=[]
            for index in range(3):
                p=Path(d)/f'body{index}.webp';Image.new('RGB',(1200,675),'blue').save(p,format='WEBP');paths.append(p)
            with self.assertRaisesRegex(AdminError,'duplicate_images'):validate_files(cover,paths)

    def test_review_required_for_every_hash_and_rubric(self):
        from nailblog.admin_images import verify_reviews
        doc=document();verify_reviews(doc,doc['image_hashes'])
        doc['image_reviews'][doc['image_hashes'][0]]['brand']=False
        with self.assertRaisesRegex(AdminError,'image_review_required'):verify_reviews(doc,doc['image_hashes'])


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_listing_reads_fresh_request_not_create_background_refresh_response(self):
        from nailblog.admin import Admin, BLOGS
        with tempfile.TemporaryDirectory() as d:
            admin=Admin(config(d));events=[];data={'items':[{'id':POST_ID}]}
            old_response=SimpleNamespace(status=200,json=AsyncMock(side_effect=ValueError('canceled refresh')))
            fresh_response=SimpleNamespace(status=200,json=AsyncMock(return_value=data))
            request=SimpleNamespace(method='GET',url=STAGING_API_ORIGIN+BLOGS+'?pageNumber=1',
                                    response=AsyncMock(return_value=fresh_response))
            class Pending:
                async def __aenter__(self):events.append('observe request');return self
                async def __aexit__(self,*args):pass
                @property
                def value(self):
                    async def received():return request
                    return received()
            def expect_request(predicate):
                self.assertTrue(predicate(request))
                for method,url in (('POST',request.url),('GET','https://api.nailhub.ai'+BLOGS),
                                   ('GET',STAGING_API_ORIGIN+BLOGS+'/'+POST_ID)):
                    self.assertFalse(predicate(SimpleNamespace(method=method,url=url)))
                return Pending()
            async def goto(*args,**kwargs):
                # The Create refresh response arrives after navigation starts;
                # response-event matching would wrongly consume this response.
                events.append('navigate');return old_response
            admin.page=SimpleNamespace(expect_request=expect_request,goto=goto,
                expect_response=unittest.mock.Mock(side_effect=AssertionError('stale response race')),
                get_by_role=lambda *args,**kwargs:SimpleNamespace(wait_for=AsyncMock()))
            self.assertEqual(await admin.list_first(),data)
            self.assertEqual(events,['observe request','navigate'])
            request.response.assert_awaited_once();fresh_response.json.assert_awaited_once()
            old_response.json.assert_not_awaited();admin.page.expect_response.assert_not_called()

    async def test_listing_missing_fresh_response_fails_without_retry(self):
        from nailblog.admin import Admin
        with tempfile.TemporaryDirectory() as d:
            admin=Admin(config(d));request=SimpleNamespace(response=AsyncMock(return_value=None))
            class Pending:
                async def __aenter__(self):return self
                async def __aexit__(self,*args):pass
                @property
                def value(self):
                    async def received():return request
                    return received()
            admin.page=SimpleNamespace(expect_request=lambda _:Pending(),goto=AsyncMock())
            with self.assertRaisesRegex(AdminError,'admin_listing_response_missing'):await admin.list_first()
            admin.page.goto.assert_awaited_once();request.response.assert_awaited_once()

    async def test_create_201_object_or_json_guid_reads_and_validates_exact_draft(self):
        from nailblog.admin import Admin
        for body in ({'id':POST_ID},POST_ID):
            with self.subTest(body=body), tempfile.TemporaryDirectory() as d:
                admin=Admin(config(d));p=payload();actual={**p,'id':POST_ID}
                admin.read=AsyncMock(return_value=actual);admin.find_drafts=AsyncMock()
                response=SimpleNamespace(status=201,json=AsyncMock(return_value=body))
                self.assertEqual(await admin.create_receipt(response,p),actual)
                admin.read.assert_awaited_once_with(POST_ID);admin.find_drafts.assert_not_awaited()

    async def test_create_unknown_201_body_reconciles_full_payload_read_only(self):
        from nailblog.admin import Admin
        for body in (None,[],42,'unknown',{}, {'id':'-'*36},ValueError('empty response')):
            with self.subTest(body=type(body).__name__), tempfile.TemporaryDirectory() as d:
                admin=Admin(config(d));p=payload();actual={**p,'id':POST_ID}
                admin.read=AsyncMock();admin.find_drafts=AsyncMock(return_value=[actual])
                parser=AsyncMock(side_effect=body) if isinstance(body,Exception) else AsyncMock(return_value=body)
                self.assertEqual(await admin.create_receipt(SimpleNamespace(status=201,json=parser),p),actual)
                admin.find_drafts.assert_awaited_once_with(p);admin.read.assert_not_awaited()

    async def test_create_unknown_response_requires_one_matching_valid_draft(self):
        from nailblog.admin import Admin
        p=payload();valid={**p,'id':POST_ID}
        changed=copy.deepcopy(valid);changed['translations'][0]['summary']='Different article'
        cases=([],[valid,valid],[{**valid,'id':'-'*36}],
               [{**valid,'status':'Published'}],[changed])
        for candidates in cases:
            with self.subTest(count=len(candidates)), tempfile.TemporaryDirectory() as d:
                admin=Admin(config(d));admin.find_drafts=AsyncMock(return_value=candidates)
                with self.assertRaises(AdminError):
                    await admin.create_receipt(SimpleNamespace(status=201,json=AsyncMock(return_value=None)),p)

    async def test_create_known_id_mismatched_readback_does_not_guess_another_draft(self):
        from nailblog.admin import Admin
        p=payload()
        for change in ({'id':CATEGORY},{'status':'Published'},{'categoryId':POST_ID}):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as d:
                admin=Admin(config(d));admin.read=AsyncMock(return_value={**p,'id':POST_ID,**change})
                admin.find_drafts=AsyncMock()
                with self.assertRaisesRegex(AdminError,'create_outcome_uncertain'):
                    await admin.create_receipt(SimpleNamespace(status=201,json=AsyncMock(return_value=POST_ID)),p)
                admin.find_drafts.assert_not_awaited()

    async def test_create_http_error_and_read_timeout_do_not_retry_or_reconcile(self):
        from nailblog.admin import Admin
        with tempfile.TemporaryDirectory() as d:
            admin=Admin(config(d));admin.read=AsyncMock(side_effect=TimeoutError());admin.find_drafts=AsyncMock()
            response=SimpleNamespace(status=500,json=AsyncMock(return_value={'id':POST_ID}))
            with self.assertRaisesRegex(AdminError,'admin_http_error'):
                await admin.create_receipt(response,payload())
            response.json.assert_not_awaited();admin.read.assert_not_awaited()
            response.status=201
            with self.assertRaises(TimeoutError):await admin.create_receipt(response,payload())
            admin.read.assert_awaited_once_with(POST_ID);admin.find_drafts.assert_not_awaited()

    async def test_get_and_upload_response_schema_stays_strict(self):
        from nailblog.admin import Admin
        with tempfile.TemporaryDirectory() as d:
            admin=Admin(config(d))
            for body in (POST_ID,None,[]):
                with self.assertRaisesRegex(AdminError,'admin_invalid_schema'):
                    await admin.json_response(SimpleNamespace(status=200,json=AsyncMock(return_value=body)))

    async def test_create_remaps_uploads_before_durable_callback_and_click(self):
        from nailblog.admin import Admin
        with tempfile.TemporaryDirectory() as d:
            admin=Admin(config(d));admin.write_ready=lambda:None;admin.ready=AsyncMock()
            calls=[];prepared=payload();new_media=[]
            for old in [prepared['coverImage'],*prepared['images']]:
                new_media.append({**old,'imageUrl':old['imageUrl'].replace('/images/','/new/')})
            async def upload(*args):return new_media[len(admin.upload.await_args_list)-1]
            admin.upload=AsyncMock(side_effect=upload);admin.paste_html=AsyncMock();admin.select_form_option=AsyncMock()
            class Control:
                def nth(self,index):return self
                async def select_option(self,value):calls.append(('select',value))
                async def fill(self,value):pass
                async def press(self,value):pass
                async def wait_for(self,**kw):pass
                async def click(self):calls.append(('click',))
                def locator(self,*a):return self
                def get_by_placeholder(self,*a,**kw):return self
                def get_by_role(self,*a,**kw):return self
            class ResponseContext:
                async def __aenter__(self):return self
                async def __aexit__(self,*a):pass
                @property
                def value(self):
                    async def received():return object()
                    return received()
            control=Control()
            admin.page=SimpleNamespace(get_by_role=lambda *a,**k:control,expect_response=lambda *a,**k:ResponseContext())
            admin.create_receipt=AsyncMock(return_value={'id':POST_ID,'status':'Draft'})
            def before(value):
                self.assertIn('/new/',value['translations'][0]['content'])
                self.assertEqual(value['images'],new_media[1:])
                calls.append(('durable',))
            await admin.save_draft(prepared,before,cover_path='cover.webp',image_paths=['1.webp','2.webp','3.webp'])
            self.assertEqual(admin.upload.await_count,4)
            self.assertEqual(calls[-2:],[('durable',),('click',)])
            self.assertEqual(prepared['images'][0]['imageUrl'],payload()['images'][0]['imageUrl'])
            receipt_payload=admin.create_receipt.await_args.args[1]
            self.assertEqual(receipt_payload['images'],new_media[1:])

    async def test_select_uses_visible_radix_control_and_verifies_native_value(self):
        from nailblog.admin import Admin
        with tempfile.TemporaryDirectory() as d:
            admin=Admin(config(d));visible=SimpleNamespace(click=AsyncMock())
            option=SimpleNamespace(click=AsyncMock())
            options=SimpleNamespace(evaluate_all=AsyncMock(return_value=[{'value':'Draft','label':'Draft'}]))
            native=SimpleNamespace(locator=lambda _:options,input_value=AsyncMock(return_value='Draft'))
            dialog=SimpleNamespace(locator=lambda _:SimpleNamespace(nth=lambda _:native),
                                   get_by_role=lambda _:SimpleNamespace(nth=lambda _:visible))
            admin.page=SimpleNamespace(get_by_role=lambda role,**kw:dialog if role=='dialog' else option)
            await admin.select_form_option(1,'Draft')
            visible.click.assert_awaited_once();option.click.assert_awaited_once()
            native.input_value=AsyncMock(return_value='Published')
            with self.assertRaisesRegex(AdminError,'form_option_not_selected'):await admin.select_form_option(1,'Draft')

    async def test_unknown_category_is_rejected_before_ui_interaction(self):
        from nailblog.admin import Admin
        with tempfile.TemporaryDirectory() as d:
            admin=Admin(config(d));options=SimpleNamespace(evaluate_all=AsyncMock(return_value=[]))
            native=SimpleNamespace(locator=lambda _:options)
            dialog=SimpleNamespace(locator=lambda _:SimpleNamespace(nth=lambda _:native),get_by_role=AsyncMock())
            admin.page=SimpleNamespace(get_by_role=lambda _:dialog)
            with self.assertRaisesRegex(AdminError,'form_option_missing'):await admin.select_form_option(0,CATEGORY)
            dialog.get_by_role.assert_not_called()

    async def test_upload_rechecks_bytes_before_opening_file_chooser(self):
        from nailblog.admin import Admin
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'changed.webp';p.write_bytes(b'changed')
            admin=Admin(config(d));admin.write_ready=lambda:None
            admin.file_hashes[str(p.resolve())]='a'*64
            admin.page=SimpleNamespace(expect_file_chooser=AsyncMock())
            with self.assertRaisesRegex(AdminError,'image_bytes_changed'):await admin.upload('Upload Images',p)
            admin.page.expect_file_chooser.assert_not_called()

    async def test_paste_rejects_actual_quill_loss(self):
        from nailblog.admin import Admin
        with tempfile.TemporaryDirectory() as d:
            admin=Admin(config(d));editor=SimpleNamespace(click=AsyncMock(),press=AsyncMock(),
                locator=lambda _:SimpleNamespace(count=AsyncMock(return_value=0)),
                inner_html=AsyncMock(return_value='<p><img src="https://staging.nailhub.ai/a.webp">caption</p>'))
            dialog=SimpleNamespace(locator=lambda _:editor)
            admin.page=SimpleNamespace(evaluate=AsyncMock(return_value=True),wait_for_function=AsyncMock(),get_by_role=lambda *_:dialog)
            with patch('nailblog.admin.asyncio.sleep',new=AsyncMock()),self.assertRaisesRegex(AdminError,'editor_roundtrip_loss'):
                await admin.paste_html('<figure><img src="https://staging.nailhub.ai/a.webp" alt="full alt"><figcaption>caption</figcaption></figure>')
            self.assertIn(unittest.mock.call('ControlOrMeta+v'),editor.press.await_args_list)


if __name__=='__main__':unittest.main()
