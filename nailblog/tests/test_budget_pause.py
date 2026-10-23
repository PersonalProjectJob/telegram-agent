"""A budget refusal BEFORE any ledger reservation or dispatch is resumable; after a reservation it is not.

Image path: ImagePipeline marks the pending candidate `paused` / `review_paused`.
Text path: OnePost.prepare ends `text_paused` when the refusal comes before the first text dispatch.
"""
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from nailblog.images import ImageError
from nailblog.images.pipeline import ImagePipeline
from nailblog.publication import AdminError
from nailblog.schedule import VN_TZ
from nailblog.usage import BudgetExceeded
from tests import test_onepost_retry as retry
from tests.test_real_images import FixtureProvider, article, config, fixture_cover

DAY1 = datetime(2026, 10, 20, 12, 0, tzinfo=VN_TZ).timestamp()


class ImagePathTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.c = config(self.temp.name)
        self.out = Path(self.temp.name) / 'output'
        self.now = DAY1
        self.provider = FixtureProvider(self.c)

    def pipeline(self):
        return ImagePipeline(self.c, provider=self.provider, cover_renderer=fixture_cover, clock=lambda: self.now)

    async def run_once(self):
        return await self.pipeline().run(article(), self.out, 'run', 1, paid_enabled=True)

    def manifest(self):
        return json.loads((self.out / 'manifest.json').read_text(encoding='utf-8'))

    def spend(self, usd):
        self.pipeline().state.add_usage(self.now, 'gemini', 'image-test', 1, usd)

    def image_calls(self):
        return sum(1 for model, _ in self.provider.calls if model == 'image-test')

    async def test_refusal_at_reserve_pauses_the_candidate_and_next_day_generates(self):
        self.spend(0.95)           # under the 1.00 cap, but one image reserves about 0.12
        with self.assertRaises(BudgetExceeded):
            await self.run_once()
        candidate = self.manifest()['candidates']['cover-r0-c0']
        self.assertEqual(candidate['status'], 'paused')
        self.assertEqual(candidate['dispatch_pause']['reason'], 'budget')
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.pipeline().ledger.report('run'), [])
        self.now += 86400          # next Vietnam day: yesterday's spend no longer counts
        document = await self.run_once()
        self.assertEqual(len(document['image_hashes']), 4)
        self.assertEqual(self.image_calls(), 4)

    async def test_refusal_at_the_cap_check_pauses_too(self):
        self.spend(1.0)            # recorder.check() refuses before the reservation
        with self.assertRaises(BudgetExceeded):
            await self.run_once()
        self.assertEqual(self.manifest()['candidates']['cover-r0-c0']['status'], 'paused')
        self.now += 86400
        self.assertEqual(len((await self.run_once())['image_hashes']), 4)

    async def test_refusal_before_the_review_pauses_it_and_resume_never_regenerates_the_image(self):
        provider, state = self.provider, self.pipeline().state
        complete = provider.complete

        def complete_then_exhaust(model, body):
            result = complete(model, body)
            if model == 'image-test' and len(provider.calls) == 1:
                state.add_usage(self.now, 'gemini', 'image-test', 1, 1.0)   # the cap is reached before the review
            return result
        provider.complete = complete_then_exhaust
        with self.assertRaises(BudgetExceeded):
            await self.run_once()
        candidate = self.manifest()['candidates']['cover-r0-c0']
        self.assertEqual(candidate['status'], 'review_paused')
        self.assertEqual(self.image_calls(), 1)
        self.now += 86400
        provider.complete = complete
        document = await self.run_once()
        self.assertEqual(len(document['image_hashes']), 4)
        self.assertEqual(self.image_calls(), 4)    # the first image was reviewed, not generated again

    async def test_refusal_after_a_reservation_keeps_the_reconciliation_path(self):
        self.provider.fail = BudgetExceeded('raised after the reservation exists')
        with self.assertRaises(BudgetExceeded):
            await self.run_once()
        self.assertEqual(self.manifest()['candidates']['cover-r0-c0']['status'], 'pending')
        self.assertEqual(self.pipeline().ledger.report('run')[0]['status'], 'uncertain')
        self.provider.fail = None
        self.now += 86400
        with self.assertRaisesRegex(ImageError, 'candidate_requires_reconciliation'):
            await self.run_once()
        self.assertEqual(len(self.provider.calls), 1)


class TextPathTests(retry.RetryBase):
    async def test_refusal_before_the_first_dispatch_ends_text_paused_and_resumes(self):
        self.service.draft = AsyncMock(side_effect=BudgetExceeded('cap'))
        with self.assertRaises(BudgetExceeded):
            await self.prepare()
        self.assertEqual(self.workflow.read('one')['phase'], 'text_paused')
        self.service.draft = AsyncMock(return_value=SimpleNamespace(article=self.article))
        result = await self.prepare()
        self.assertEqual(result['phase'], 'images_ready')
        self.service.draft.assert_awaited_once()

    async def test_refusal_after_a_dispatch_keeps_the_reconciliation_path(self):
        async def dispatch_then_refuse(topic, model, **_kw):
            model.before_dispatch()                 # one text dispatch happened
            raise BudgetExceeded('cap')
        self.service.draft = AsyncMock(side_effect=dispatch_then_refuse)
        with self.assertRaises(BudgetExceeded):
            await self.prepare()
        self.assertEqual(self.workflow.read('one')['phase'], 'text_pending')
        self.service.draft = AsyncMock(return_value=SimpleNamespace(article=self.article))
        with self.assertRaisesRegex(AdminError, 'text_requires_manual_reconciliation'):
            await self.prepare()
        self.service.draft.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
