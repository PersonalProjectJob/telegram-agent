"""Explicit workflow recovery and environment contracts; no model or website calls."""
import copy
import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from nailblog.admin_images import validate_files
from nailblog.admin_security import Guard
from nailblog.config import API_ORIGIN, SITE_ORIGIN, PRODUCTION_MEDIA_ORIGIN, ContractConfig
from nailblog.images.post import crop, webp
from nailblog.onepost import OnePost
from nailblog.publication import AdminError, binding, digest, instant, safe_url, same_html
from nailblog.service import Service
from tests.fakes import make_article
from tests.helpers import settings
from tests.test_real_images import fixture_image


class EnvironmentTests(unittest.TestCase):
    def test_production_binding_and_media_never_accept_staging(self):
        with tempfile.TemporaryDirectory() as d:
            c=settings(d,contract=ContractConfig(media_origin=PRODUCTION_MEDIA_ORIGIN))
            self.assertEqual(binding(c)['environment'],'production')
            self.assertEqual(safe_url(PRODUCTION_MEDIA_ORIGIN+'/one.webp',c),PRODUCTION_MEDIA_ORIGIN+'/one.webp')
            for url in ('https://vlink-storages-test.sgp1.cdn.digitaloceanspaces.com/one.webp',
                        'https://api-staging.nailhub.ai/one.webp'):
                with self.assertRaises(AdminError):safe_url(url,c)
            with self.assertRaises(AdminError):binding(replace(c,contract=replace(c.contract,site_origin='https://staging.nailhub.ai')))

    def test_naive_timestamp_remains_rejected(self):
        with self.assertRaisesRegex(AdminError,'invalid_timestamp'):instant('2026-10-19T10:00:00')

    def test_only_exact_blog_internal_figure_attrs_are_canonical(self):
        canonical='<figure><img src="https://nailhub.ai/one.webp" alt="Mẫu móng tiếng Việt"><figcaption>Ảnh</figcaption></figure>'
        live=canonical.replace('<figure>','<figure class="blog-image-figure" contenteditable="false">')
        self.assertTrue(same_html(live,canonical))
        for changed in (live.replace('blog-image-figure','blog-image-figure injected'),
                        live.replace('false','true'),live.replace('Ảnh','Khác'),
                        live.replace('alt="Mẫu móng tiếng Việt"','alt="Nội dung khác"')):
            self.assertFalse(same_html(changed,canonical))

    def test_quill_exact_safety_link_attrs_preserve_href_text_and_unknown_attrs(self):
        source='<p><a href="https://nailhub.ai/">Đọc thêm</a></p>'
        safe=source.replace('<a ','<a rel="noopener noreferrer" target="_blank" ')
        self.assertTrue(same_html(source,safe))
        self.assertTrue(same_html(source,safe.replace(' target="_blank"','')))
        for changed in (safe.replace('_blank','_self'),safe.replace('noreferrer','noreferrer nofollow'),
                        safe.replace('nailhub.ai','evil.example'),safe.replace('Đọc thêm','Khác')):
            self.assertFalse(same_html(source,changed))

    def test_only_exact_standalone_image_class_is_canonical(self):
        source='<p><img src="https://nailhub.ai/a.webp" alt="Mẫu móng" width="1200" height="675" title="Màu hồng" loading="lazy"></p>'
        live=source.replace('<img ','<img class="blog-standalone-image" ')
        self.assertTrue(same_html(source,live))
        self.assertTrue(same_html(live,source))
        for changed in (live.replace('blog-standalone-image','blog-standalone-image extra'),
                        live.replace('blog-standalone-image','other-image'),
                        live.replace(' title="Màu hồng"',''),live.replace('Màu hồng','Màu đỏ'),
                        live.replace(' loading="lazy"',''),live.replace('lazy','eager'),
                        live.replace('alt="Mẫu móng"','alt="Khác"'),
                        live.replace('width="1200"','width="600"'),
                        live.replace('a.webp','b.webp'),live.replace('<img ','<img data-extra="1" ')):
            self.assertFalse(same_html(source,changed))
        self.assertFalse(same_html(source,source.replace('<p>','<p class="blog-standalone-image">')))

    def test_only_one_exact_terminal_quill_caret_line_after_figure_is_ignored(self):
        figure='<figure><img src="https://nailhub.ai/a.webp" alt="Mẫu móng tay có màu hồng"><figcaption>Ảnh minh họa</figcaption></figure>'
        self.assertTrue(same_html(figure,figure+'<p><br></p>'))
        self.assertTrue(same_html(figure+'<p><br></p>',figure))
        for extra in ('<p>Chữ</p>','<p class="extra"><br></p>','<p><br class="extra"></p>',
                      '<p><br></p><p><br></p>','<p> </p>'):
            self.assertFalse(same_html(figure,figure+extra))
        self.assertFalse(same_html('<p>Trước</p>','<p>Trước</p><p><br></p>'))
        self.assertFalse(same_html(figure,figure.replace('<figcaption>Ảnh minh họa</figcaption>','')+'<p><br></p>'))
        self.assertFalse(same_html(figure,figure.replace('Ảnh minh họa','Khác')+'<p><br></p>'))


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.config=settings(self.temp.name,mode='live',contract=ContractConfig(verified=True,media_origin=PRODUCTION_MEDIA_ORIGIN))
        self.service=Service(self.config,clock=lambda:1792458000)
        self.service.state.set_paused(False)
        self.service.save_cache([],[])
        self.topic=self.service.state.add_topic('2026-W43','Màu nail merlot','nail-salon','2026-10-25T19:00+07:00',state='approved')
        self.workflow=OnePost(self.service)
        self.article=make_article()
        self.directory=self.root/'accepted';self.directory.mkdir()
        paths=[]
        for i in range(4):
            path=self.directory/('cover.webp' if i==0 else f'body-{i}.webp')
            path.write_bytes(webp(crop(fixture_image(i+40),(1600,900) if i==0 else (1200,675)),200*1024 if i==0 else 150*1024));paths.append(str(path))
        hashes=validate_files(paths[0],paths[1:])
        metadata=[{'src':Path(p).name,'alt':slot['image_slot']['alt'],'caption':slot['image_slot'].get('caption',''),'width':1200,'height':675}
                  for p,slot in zip(paths[1:],[s for s in self.article['sections'] if s.get('image_slot')])]
        self.document={'article':self.article,'cover_path':paths[0],'image_paths':paths[1:],
                       'image_hashes':hashes,'image_reviews':{h:dict.fromkeys(('technical','anatomy','topic','brand','distinct'),True) for h in hashes},'images':metadata}
        self.document_file=self.directory/'document.json';self.write_document()

    def write_document(self):
        self.document_file.write_text(json.dumps(self.document),encoding='utf-8')
        entries={role:{'status':'approved','sha':sha,'intent':'fixture-generated-'+role,'review_intent':'fixture-reviewed-'+role,
                       'scores':dict.fromkeys(('technical','anatomy','topic','brand','distinct'),True)}
                 for role,sha in zip(['cover','body-1','body-2','body-3'],self.document['image_hashes'])}
        (self.directory/'manifest.json').write_text(json.dumps({'status':'complete','positions':entries,'document_digest':digest(self.document)}),encoding='utf-8')

    async def prepare(self,**extra):
        return await self.workflow.prepare('one',self.topic,self.root/'out',article=self.article,
                                           images_document=str(self.document_file),**extra)

    async def test_prepare_import_then_sha_image_approval_is_explicit_and_resume_has_one_local_post(self):
        first=await self.prepare()
        self.assertEqual(first['phase'],'images_ready')
        with self.assertRaisesRegex(AdminError,'manual_image_acceptance_required'):
            await self.workflow.save('one','87654321-1234-1234-1234-123456789abc')
        with self.assertRaisesRegex(AdminError,'image_approval_snapshot_changed'):
            self.workflow.accept_images('one','wrong')
        accepted=self.workflow.accept_images('one',first['snapshot'])
        again=await OnePost(self.service).prepare('one',self.topic,self.root/'out',article=self.article,images_document=str(self.document_file))
        self.assertEqual(accepted['phase'],'images_approved');self.assertEqual(again['post_id'],first['post_id'])
        self.assertEqual(len(self.service.state.list_posts()),1)

    async def test_held_manual_curated_document_cannot_be_imported_even_with_five_true_scores(self):
        self.document.update(status='manual_curated_preview',automatic_flow_verdict='HELD');self.write_document()
        with self.assertRaisesRegex(AdminError,'complete_reviewed_image_provenance_required'):await self.prepare()
        self.assertIsNone(self.workflow.read('one'))

    async def test_tampered_bytes_block_reviewed_resume(self):
        await self.prepare();Path(self.document['image_paths'][0]).write_bytes(b'changed')
        with self.assertRaises(AdminError):await self.prepare()

    async def test_changed_provenance_and_article_are_not_silently_accepted(self):
        await self.prepare()
        self.article['title']='Một tiêu đề khác'
        with self.assertRaisesRegex(AdminError,'onepost_binding_changed'):await self.prepare()

    async def test_pause_blocks_preparation_before_any_model(self):
        self.service.state.set_paused(True)
        with self.assertRaisesRegex(AdminError,'explicit_live_unpaused_required'):await self.prepare()

    async def test_text_intent_failure_is_not_retried_after_restart(self):
        self.service.draft=AsyncMock(side_effect=RuntimeError('fixture interrupted'))
        with self.assertRaises(RuntimeError):
            await self.workflow.prepare('text',self.topic,self.root/'text',free_enabled=True,ceiling=100)
        with self.assertRaisesRegex(AdminError,'text_requires_manual_reconciliation'):
            await OnePost(self.service).prepare('text',self.topic,self.root/'text',free_enabled=True,ceiling=100)
        self.service.draft.assert_awaited_once()
        self.assertEqual(self.workflow.artifact('text')['phase'],'text_pending')

    async def test_same_topic_cannot_create_a_second_post_under_another_run_id(self):
        await self.prepare()
        with self.assertRaisesRegex(AdminError,'topic_already_claimed'):
            await self.workflow.prepare('second',self.topic,self.root/'other',article=self.article,images_document=str(self.document_file))

    async def test_distinct_run_ids_cannot_both_spend_before_local_post_exists(self):
        entered,release=asyncio.Event(),asyncio.Event()
        async def writing(*_args,**_kwargs):
            entered.set();await release.wait();raise RuntimeError('fixture interrupted after one text dispatch')
        self.service.draft=AsyncMock(side_effect=writing)
        first=asyncio.create_task(self.workflow.prepare('winner',self.topic,self.root/'first',free_enabled=True,ceiling=200))
        await entered.wait()
        try:
            self.assertEqual(self.service.state.list_posts(),[])
            with self.assertRaisesRegex(AdminError,'topic_already_claimed'):
                await OnePost(self.service).prepare('loser',self.topic,self.root/'second',free_enabled=True,ceiling=200)
        finally:release.set()
        with self.assertRaises(RuntimeError):await first
        self.service.draft.assert_awaited_once()
        self.assertIsNone(self.workflow.read('loser'))
        with self.service.state.connection() as db:
            self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM onepost_topic_claims')],[(self.topic,'winner')])

    async def test_verified_local_document_is_immutable_once_create_intent_exists(self):
        result=await self.prepare()
        self.service.state.transition(result['post_id'],'review')
        with self.assertRaises(ValueError):self.service.state.set_post_document(result['post_id'],{})

    async def test_onepost_create_timeout_then_real_service_recovery_never_reuploads_or_creates(self):
        review=await self.prepare();self.workflow.accept_images('one',review['snapshot'])
        remote={}
        async def save(payload,before_create,**_kwargs):
            uploaded=copy.deepcopy(payload)
            uploaded['translations'][0]['content']=uploaded['translations'][0]['content'].replace('/nailblog-local/','/uploaded/')
            for image in [uploaded['coverImage'],*uploaded['images']]:
                image['imageUrl']=image['imageUrl'].replace('/nailblog-local/','/uploaded/')
            before_create(uploaded)
            remote.update(uploaded,id='12345678-1234-1234-1234-123456789abc')
            raise TimeoutError('fixture unknown Create outcome')
        fake=SimpleNamespace(ready=AsyncMock(),guard=SimpleNamespace(),save_draft=AsyncMock(side_effect=save),
                             upload=AsyncMock(),find_drafts=AsyncMock(side_effect=lambda _: [remote]),
                             read=AsyncMock(side_effect=lambda _: remote))
        context=AsyncMock();context.__aenter__.return_value=fake;context.write_ready=lambda:None
        with patch('nailblog.admin.Admin',return_value=context):
            with self.assertRaises(TimeoutError):
                await self.workflow.save('one','87654321-1234-1234-1234-123456789abc')
            restarted=OnePost(Service(self.config,clock=self.service.clock))
            result=await restarted.save('one','87654321-1234-1234-1234-123456789abc')
            again=await restarted.save('one','87654321-1234-1234-1234-123456789abc')
        self.assertEqual(result['admin_id'],remote['id']);self.assertEqual(again['admin_id'],remote['id'])
        fake.save_draft.assert_awaited_once();fake.upload.assert_not_awaited()
        fake.find_drafts.assert_awaited_once();fake.read.assert_awaited_once()
        with self.assertRaisesRegex(AdminError,'draft_approval_snapshot_changed'):
            restarted.approve('one','stale')
        approved=restarted.approve('one',again['snapshot'])
        self.assertEqual(approved['phase'],'scheduled')

    async def test_generation_resume_uses_same_image_run_and_never_rewrites_text(self):
        self.service.draft=AsyncMock(return_value=SimpleNamespace(article=self.article))
        pipeline=SimpleNamespace(run=AsyncMock(side_effect=AdminError('fixture held generation')))
        with patch('nailblog.onepost.ImagePipeline',return_value=pipeline):
            for _ in range(2):
                with self.assertRaisesRegex(AdminError,'fixture held generation'):
                    await OnePost(self.service).prepare('generated',self.topic,self.root/'generated',free_enabled=True,ceiling=200)
        self.service.draft.assert_awaited_once()
        self.assertEqual(pipeline.run.await_count,2)
        self.assertEqual([call.args[2] for call in pipeline.run.await_args_list],['generated-images','generated-images'])


if __name__=='__main__':unittest.main()
