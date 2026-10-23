"""S7: one-post image retry, mark_published and the before_dispatch hook (real State, fake image pipeline)."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from nailblog.admin_images import validate_files
from nailblog.config import PRODUCTION_MEDIA_ORIGIN, ContractConfig
from nailblog.images import ImageError
from nailblog.images.pipeline import DispatchPaused
from nailblog.images.post import crop, webp
from nailblog.onepost import OnePost
from nailblog.publication import AdminError, digest
from nailblog.service import Service
from tests.fakes import make_article
from tests.helpers import settings
from tests.test_real_images import fixture_image

SCORES = dict.fromkeys(('technical', 'anatomy', 'topic', 'brand', 'distinct'), True)


class FakePipeline:
    """Stands in for ImagePipeline: writes a complete reviewed image set per generation."""

    def __init__(self, generations=2):
        self.generations, self.runs, self.rejections = generations, [], []

    def __call__(self, config, **kwargs):
        self.config = config
        return self

    async def run(self, article, out, run_id, ceiling, *, paid_enabled=False, free_enabled=False, **_kw):
        generation = len(self.runs)
        self.runs.append({'out': out, 'run_id': run_id, 'ceiling': ceiling,
                          'paid_enabled': paid_enabled, 'free_enabled': free_enabled})
        if generation >= self.generations:
            raise ImageError('no_untried_candidates')
        out.mkdir(parents=True, exist_ok=True)
        paths = []
        for i in range(4):
            path = out/('cover.webp' if i == 0 else f'body-{i}.webp')
            path.write_bytes(webp(crop(fixture_image(i+40+10*generation), (1600, 900) if i == 0 else (1200, 675)),
                                  200*1024 if i == 0 else 150*1024))
            paths.append(str(path))
        hashes = validate_files(paths[0], paths[1:])
        slots = [s for s in article['sections'] if s.get('image_slot')]
        metadata = [{'src': Path(p).name, 'alt': s['image_slot']['alt'], 'caption': s['image_slot'].get('caption', ''),
                     'width': 1200, 'height': 675} for p, s in zip(paths[1:], slots)]
        document = {'article': article, 'cover_path': paths[0], 'image_paths': paths[1:], 'image_hashes': hashes,
                    'image_reviews': {h: dict(SCORES) for h in hashes}, 'images': metadata}
        (out/'document.json').write_text(json.dumps(document), encoding='utf-8')
        entries = {role: {'status': 'approved', 'sha': sha, 'intent': 'fixture-generated-'+role,
                          'review_intent': 'fixture-reviewed-'+role, 'scores': dict(SCORES)}
                   for role, sha in zip(['cover', 'body-1', 'body-2', 'body-3'], hashes)}
        (out/'manifest.json').write_text(json.dumps({'status': 'complete', 'positions': entries,
                                                     'document_digest': digest(document)}), encoding='utf-8')
        return document

    def reject_selected(self, out, position, expected_sha, reason):
        self.rejections.append((Path(out), position, expected_sha, reason))
        return {}


class RetryBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = settings(self.temp.name, mode='live',
                               contract=ContractConfig(verified=True, media_origin=PRODUCTION_MEDIA_ORIGIN))
        self.service = Service(self.config, clock=lambda: 1792458000)
        self.service.state.set_paused(False)
        self.service.save_cache([], [])
        self.topic = self.service.state.add_topic('2026-W43', 'Màu nail merlot', 'nail-salon',
                                                  '2026-10-25T19:00+07:00', state='approved')
        self.workflow = OnePost(self.service)
        self.article = make_article()
        self.service.draft = AsyncMock(return_value=SimpleNamespace(article=self.article))
        self.pipeline = FakePipeline()
        patcher = patch('nailblog.onepost.ImagePipeline', self.pipeline)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def prepare(self, **extra):
        return await self.workflow.prepare('one', self.topic, self.root/'out', paid_enabled=True, ceiling=150, **extra)


class RetryImagesTests(RetryBase):
    async def test_one_retry_gives_new_snapshot_same_limits_then_exhausted(self):
        first = await self.prepare()
        self.assertEqual(first['phase'], 'images_ready')
        second = await self.workflow.retry_images('one', '  cover looks wrong\n ')
        self.assertEqual(second['phase'], 'images_ready')
        self.assertNotEqual(second['snapshot'], first['snapshot'])
        self.assertTrue(set(second['image_hashes']).isdisjoint(first['image_hashes']))
        self.assertEqual(self.pipeline.runs[0].pop('out'), self.pipeline.runs[1].pop('out'))
        self.assertEqual(self.pipeline.runs[0], self.pipeline.runs[1])
        self.assertEqual(self.pipeline.runs[1], {'run_id': 'one-images', 'ceiling': 150,
                                                 'paid_enabled': True, 'free_enabled': False})
        self.assertEqual([(r[1], r[2]) for r in self.pipeline.rejections],
                         list(zip(['cover', 'body-1', 'body-2', 'body-3'], first['image_hashes'])))
        self.assertTrue(all(r[3] == 'cover looks wrong' for r in self.pipeline.rejections))
        document = json.loads(self.workflow.read('one')['document'])
        self.assertEqual(document['image_retries'], 1)
        self.assertEqual(document['image_retry_reasons'], ['cover looks wrong'])
        self.assertEqual(len(self.service.state.list_posts()), 1)
        with self.assertRaisesRegex(AdminError, 'image_retry_exhausted'):
            await self.workflow.retry_images('one', 'again')
        self.assertEqual(len(self.pipeline.runs), 2)
        self.assertEqual(len(self.pipeline.rejections), 4)
        # the new snapshot is approvable
        self.assertEqual(self.workflow.accept_images('one', second['snapshot'])['phase'], 'images_approved')

    async def test_retry_after_image_approval_is_not_allowed(self):
        first = await self.prepare()
        self.workflow.accept_images('one', first['snapshot'])
        with self.assertRaisesRegex(AdminError, 'image_retry_not_allowed'):
            await self.workflow.retry_images('one', 'too late')
        self.assertEqual(self.pipeline.rejections, [])
        self.assertEqual(len(self.pipeline.runs), 1)

    async def test_retry_unknown_run_is_not_allowed(self):
        with self.assertRaisesRegex(AdminError, 'image_retry_not_allowed'):
            await self.workflow.retry_images('missing', 'x')

    async def test_pipeline_error_without_untried_candidates_is_surfaced_unchanged(self):
        self.pipeline.generations = 1
        await self.prepare()
        with self.assertRaisesRegex(ImageError, 'no_untried_candidates'):
            await self.workflow.retry_images('one', 'nope')
        run = self.workflow.read('one')
        self.assertEqual(run['phase'], 'images_pending')
        with self.assertRaisesRegex(AdminError, 'image_retry_not_allowed'):
            await self.workflow.retry_images('one', 'again')


class MarkPublishedTests(RetryBase):
    async def test_refused_until_post_is_published_and_never_touches_admin(self):
        review = await self.prepare()
        state, post = self.service.state, review['post_id']
        with patch('nailblog.admin.Admin', side_effect=AssertionError('admin touched')), \
                patch.object(self.service, 'publish_pass', side_effect=AssertionError('publish touched')):
            for target in (None, 'review', 'scheduled', 'before_publish'):
                if target:
                    state.transition(post, target)
                with self.assertRaisesRegex(AdminError, 'mark_published_requires_published'):
                    self.workflow.mark_published('one')
                self.assertNotEqual(self.workflow.read('one')['phase'], 'published')
            state.transition(post, 'published', evidence={'readback': 'Published'})
            result = self.workflow.mark_published('one')
        self.assertEqual(result['phase'], 'published')
        self.assertEqual(self.workflow.read('one')['phase'], 'published')

    async def test_unknown_run_is_refused(self):
        with self.assertRaisesRegex(AdminError, 'mark_published_requires_published'):
            self.workflow.mark_published('missing')


class BeforeDispatchTests(RetryBase):
    def draft_through_hook(self):
        """Mirror text.py: a hook failure becomes DispatchPaused before the transport is used."""
        calls = []

        async def draft(topic, model, **_kw):
            try:
                model.before_dispatch()
            except Exception:
                raise DispatchPaused('workflow_paused_before_dispatch') from None
            calls.append(1)
            return SimpleNamespace(article=self.article)
        self.service.draft = AsyncMock(side_effect=draft)
        return calls

    async def test_false_before_first_text_dispatch_pauses_and_resume_works(self):
        calls = self.draft_through_hook()
        with self.assertRaises(DispatchPaused):
            await self.prepare(before_dispatch=lambda: False)
        self.assertEqual(calls, [])
        run = self.workflow.read('one')
        self.assertEqual(run['phase'], 'text_paused')
        self.assertFalse(json.loads(run['document']).get('text_dispatches'))
        resumed = await self.prepare(before_dispatch=lambda: True)
        self.assertEqual(resumed['phase'], 'images_ready')
        self.assertEqual(calls, [1])

    async def test_raising_hook_pauses_like_false(self):
        calls = self.draft_through_hook()

        def boom():
            raise RuntimeError('stop requested')
        with self.assertRaises(DispatchPaused):
            await self.prepare(before_dispatch=boom)
        self.assertEqual(calls, [])
        self.assertEqual(self.workflow.read('one')['phase'], 'text_paused')

    async def test_default_none_keeps_behaviour(self):
        calls = self.draft_through_hook()
        result = await self.prepare()
        self.assertEqual(result['phase'], 'images_ready')
        self.assertEqual(calls, [1])


if __name__ == '__main__':
    unittest.main()
