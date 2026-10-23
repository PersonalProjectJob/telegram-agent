"""S1: paid USD ledger hardening (stale reconcile, age-out, unit-price settlement) and the Gemini env key.
All providers and pixels are fixtures; the ledger is the real PaidLedger on a temporary SQLite DB."""
import asyncio
import base64
import json
import logging
import tempfile
import unittest
from dataclasses import asdict, replace
from datetime import datetime
from importlib.resources import files
from pathlib import Path

from nailblog import env_secrets
from nailblog.config import BudgetConfig, ImageConfig
from nailblog.images import ImageError
from nailblog.images.pipeline import ImagePipeline, STALE_MARGIN_S
from nailblog.images.post import VERSION
from nailblog.images.prompts import REVIEW_VERSION, VERSION as PROMPT_VERSION
from nailblog.images.provider import GeminiImages
from nailblog.paid import PaidLedger
from nailblog.publication import digest
from nailblog.schedule import VN_TZ
from nailblog.state import State
from nailblog.usage import BudgetExceeded, day_bounds
from tests.helpers import settings
from tests.test_real_images import article, envelope, fixture_cover, fixture_image

SECRET = 'AIza-fixture-secret-key-0123456789'


def make_config(directory, *, daily=1.0, budget_extra=None, **image):
    budget = BudgetConfig(daily, {'image-test': (.5, 3), 'review-test': (.3, 2.5)}, {}, {'image-test': 60},
                          **(budget_extra or {}))
    return settings(directory, image=ImageConfig(model_body='image-test', model_cover='image-test',
                                                 review_model='review-test', candidates=1, max_rounds=1, **image),
                    budget=budget)


class CountingProvider(GeminiImages):
    """Records every transport-level call; count/complete return canned envelopes."""
    def __init__(self, config, meta=None):
        super().__init__(config)
        self.counts, self.completes, self.verifies, self.meta = 0, 0, 0, meta

    def verify_models(self):
        self.verifies += 1

    def count(self, model, body, ceiling):
        self.counts += 1
        return 100

    def complete(self, model, body):
        self.completes += 1
        part = {'inlineData': {'mimeType': 'image/png', 'data': base64.b64encode(fixture_image(1)).decode()}}
        return envelope([part], meta=self.meta)


def image_body(size):
    return {'contents': [{'role': 'user', 'parts': [{'text': 'x'}]}],
            'generationConfig': {'imageConfig': {'aspectRatio': '16:9', 'imageSize': size}}}


class LedgerHardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.c = make_config(self.temp.name)
        self.state = State(self.c.db_path)
        self.t0 = datetime(2026, 10, 20, 12, 0, tzinfo=VN_TZ).timestamp()
        self.now = self.t0
        self.ledger = PaidLedger(self.state, self.c, lambda: self.now)
        self.ledger.run('run', 'binding', 1)

    def restart(self):
        return PaidLedger(State(self.c.db_path), self.c, lambda: self.now)

    def spent(self, ts):
        return self.state.usd_between(*day_bounds(ts))

    def test_crash_restart_pending_becomes_uncertain_still_counted_then_ages_out(self):
        self.ledger.reserve('crashed', 'gemini', 'image-test', .8, run_id='run')   # process dies here, still pending
        self.now = self.t0 + 3600
        fresh = self.restart()
        self.assertEqual(fresh.reconcile_stale(self.now, 780), 1)
        row = fresh.get('crashed')
        self.assertEqual((row['status'], row['reserved'], row['charged']), ('uncertain', 800000, 0))
        with self.assertRaises(BudgetExceeded):
            fresh.reserve('next', 'gemini', 'image-test', .3, run_id='run')
        # not old enough to age out yet: still uncertain and still counted
        self.assertEqual(fresh.age_out_uncertain(self.now), 0)
        with self.assertRaises(BudgetExceeded):
            fresh.reserve('next', 'gemini', 'image-test', .3, run_id='run')
        # next Vietnam day, more than 24h later: full bound lands on the ORIGINAL day, today's cap is released
        self.now = self.t0 + 86400 + 60
        self.assertEqual(fresh.age_out_uncertain(self.now), 1)
        row = fresh.get('crashed')
        self.assertEqual((row['status'], row['charged']), ('settled', 800000))
        self.assertEqual(json.loads(row['metadata']), {'assumed_full_bound': True})
        self.assertAlmostEqual(self.spent(self.t0), .8, places=6)
        self.assertEqual(self.spent(self.now), 0)
        usage = self.state.usage_between(0, self.now + 1)
        self.assertEqual([r[0] if not hasattr(r, 'keys') else r['at'] for r in usage], [self.t0])
        fresh.run('run2', 'b2', 1)
        fresh.reserve('today', 'gemini', 'image-test', .9, run_id='run2')          # cap released
        self.assertEqual(fresh.age_out_uncertain(self.now), 0)                      # idempotent

    def test_reconcile_stale_only_touches_old_pending(self):
        self.ledger.reserve('old', 'gemini', 'image-test', .1, run_id='run')
        self.now = self.t0 + 1000
        self.ledger.reserve('young', 'gemini', 'image-test', .1, run_id='run')
        self.assertEqual(self.ledger.reconcile_stale(self.now, 780), 1)
        self.assertEqual(self.ledger.get('old')['status'], 'uncertain')
        self.assertEqual(self.ledger.get('young')['status'], 'pending')
        self.ledger.settle('young', 1, .05)
        self.assertEqual(self.ledger.reconcile_stale(self.now + 10**6, 780), 0)     # settled is never touched
        self.assertEqual(self.ledger.get('young')['status'], 'settled')

    def test_aged_out_charge_is_never_lower_than_reservation_and_counts_today_when_same_day(self):
        self.ledger.reserve('u', 'gemini', 'image-test', .4, run_id='run')
        self.ledger.uncertain('u')
        self.now = self.t0 + 86400 + 1
        self.ledger.age_out_uncertain(self.now)
        self.assertEqual(self.ledger.get('u')['charged'], self.ledger.get('u')['reserved'])
        self.assertGreaterEqual(self.spent(self.t0) * 1e6, self.ledger.get('u')['reserved'])

    def test_unsettled_intent_counts_towards_cap_and_spend_is_never_lowered(self):
        self.ledger.reserve('a', 'gemini', 'image-test', .5, run_id='run')
        self.ledger.settle('a', 1, .3)
        self.ledger.reserve('b', 'gemini', 'image-test', .4, run_id='run')         # pending
        with self.assertRaises(BudgetExceeded):
            self.ledger.reserve('c', 'gemini', 'image-test', .4, run_id='run')     # .3 + .4 + .4 > 1.0
        self.ledger.reserve('c', 'gemini', 'image-test', .3, run_id='run')         # .3 + .4 + .3 == 1.0

    def test_pipeline_run_start_reconciles_and_ages_out(self):
        now = self.t0 + 5 * 86400
        clock = lambda: now
        ledger = PaidLedger(self.state, self.c, lambda: self.t0)
        ledger.reserve('stale', 'gemini', 'image-test', .2, run_id='run')           # pending since t0
        ledger.reserve('ancient', 'gemini', 'image-test', .1, run_id='run')
        ledger.uncertain('ancient')
        ledger.run('other', 'x', 1)
        ledger.reserve('fresh', 'gemini', 'image-test', .1, run_id='other')         # reserved at t0 too
        provider = CountingProvider(self.c)
        provider.verify_models = lambda: (_ for _ in ()).throw(ImageError('stop_after_recovery'))
        pipeline = ImagePipeline(self.c, state=self.state, provider=provider, clock=clock, cover_renderer=fixture_cover)
        with self.assertRaisesRegex(ImageError, 'stop_after_recovery'):
            asyncio.run(pipeline.run(article(), Path(self.temp.name) / 'out', 'r1', 1, paid_enabled=True))
        # everything is older than a day here, so the stale one is uncertain then aged out in the same call
        for name in ('stale', 'ancient', 'fresh'):
            row = pipeline.ledger.get(name)
            self.assertEqual((row['status'], row['charged'] == row['reserved']), ('settled', True))
            self.assertEqual(json.loads(row['metadata']), {'assumed_full_bound': True})
        self.assertAlmostEqual(self.spent(self.t0), .4, places=6)

    def test_pipeline_run_start_marks_stale_pending_uncertain_and_keeps_counting(self):
        timeout = self.c.image.timeout_seconds
        now = self.t0 + timeout + STALE_MARGIN_S + 1
        PaidLedger(self.state, self.c, lambda: self.t0).reserve('stale', 'gemini', 'image-test', .6, run_id='run')
        PaidLedger(self.state, self.c, lambda: now - 10).reserve('live', 'gemini', 'image-test', .1, run_id='run')
        provider = CountingProvider(self.c)
        provider.verify_models = lambda: (_ for _ in ()).throw(ImageError('stop_after_recovery'))
        pipeline = ImagePipeline(self.c, state=self.state, provider=provider, clock=lambda: now,
                                 cover_renderer=fixture_cover)
        with self.assertRaisesRegex(ImageError, 'stop_after_recovery'):
            asyncio.run(pipeline.run(article(), Path(self.temp.name) / 'out', 'r1', 1, paid_enabled=True))
        self.assertEqual(pipeline.ledger.get('stale')['status'], 'uncertain')
        self.assertEqual(pipeline.ledger.get('live')['status'], 'pending')
        with self.assertRaises(BudgetExceeded):
            pipeline.ledger.reserve('big', 'gemini', 'image-test', .5, run_id='run')


class UnitPriceSettlementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def pipeline(self, unit_prices, *, daily=1.0, meta=None):
        config = make_config(self.temp.name, daily=daily, budget_extra={'image_unit_prices': unit_prices})
        provider = CountingProvider(config, meta)
        pipeline = ImagePipeline(config, provider=provider, cover_renderer=fixture_cover)
        pipeline.ledger.run('run', 'b', 1)
        return pipeline, provider

    def paid(self, pipeline, size='1K', intent='i', in_ceiling=100, out_ceiling=100):
        return asyncio.run(pipeline.paid('run', intent, 'image-test', image_body(size), in_ceiling, out_ceiling, image=True))

    def test_unit_price_above_token_cost_wins_and_is_reserved_up_front(self):
        pipeline, provider = self.pipeline({'image-test': {'1K': .05}})
        self.paid(pipeline)
        row = pipeline.ledger.get('i')
        # token cost = (100*.5 + 100*60)/1e6 = 0.00605; unit price .05 is higher
        self.assertEqual((row['reserved'], row['charged'], row['status']), (50000, 50000, 'settled'))
        basis = json.loads(row['metadata'])['unit_price_basis']
        self.assertEqual(basis, {'token_cost_usd': 0.00605, 'unit_price_usd': .05, 'size': '1K', 'settled_usd': .05})
        self.assertLessEqual(row['charged'], row['reserved'])
        self.assertEqual((provider.counts, provider.completes), (1, 1))

    def test_token_cost_above_unit_price_wins(self):
        pipeline, _ = self.pipeline({'image-test': {'1K': .001}})
        self.paid(pipeline)
        row = pipeline.ledger.get('i')
        self.assertEqual(row['charged'], 6050)
        self.assertLessEqual(row['charged'], row['reserved'])
        basis = json.loads(row['metadata'])['unit_price_basis']
        self.assertEqual((basis['token_cost_usd'], basis['unit_price_usd'], basis['settled_usd']), (.00605, .001, .00605))

    def test_no_unit_price_for_model_or_size_keeps_token_settlement_exactly(self):
        for prices in ({}, {'other-model': {'1K': .5}}, {'image-test': {'2K': .5}}):
            with self.subTest(prices=prices):
                pipeline, _ = self.pipeline(prices)
                self.paid(pipeline, intent='i'+str(len(str(prices))))
                row = pipeline.ledger.get('i'+str(len(str(prices))))
                self.assertEqual((row['reserved'], row['charged']), (6050, 6050))
                self.assertNotIn('unit_price_basis', json.loads(row['metadata']))
                pipeline.ledger.state  # same DB reused across subtests, intents are unique

    def test_token_usage_above_ceiling_still_raises_and_keeps_full_reservation(self):
        pipeline, _ = self.pipeline({'image-test': {'1K': .05}},
                                    meta={'promptTokenCount': 100, 'candidatesTokenCount': 101, 'thoughtsTokenCount': 0})
        with self.assertRaisesRegex(ImageError, 'reported_usage_exceeds_reserved_bound'):
            self.paid(pipeline)
        row = pipeline.ledger.get('i')
        self.assertEqual((row['status'], row['reserved'], row['charged']), ('uncertain', 50000, 0))

    def test_cap_reached_raises_before_any_transport_call(self):
        pipeline, provider = self.pipeline({'image-test': {'1K': .05}}, daily=.5)
        pipeline.ledger.run('hog', 'h', .5)
        pipeline.ledger.reserve('hog1', 'gemini', 'image-test', .5, run_id='hog')
        with self.assertRaises(BudgetExceeded):
            self.paid(pipeline)
        self.assertEqual((provider.counts, provider.completes, provider.verifies), (0, 0, 0))
        self.assertIsNone(pipeline.ledger.get('i'))

    def test_unit_price_that_would_cross_the_cap_is_refused_before_generation(self):
        pipeline, provider = self.pipeline({'image-test': {'1K': .05}}, daily=.04)
        with self.assertRaises(BudgetExceeded):
            self.paid(pipeline)
        self.assertEqual(provider.completes, 0)
        self.assertIsNone(pipeline.ledger.get('i'))


class EnvKeyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        saved = env_secrets._captured
        self.addCleanup(setattr, env_secrets, '_captured', saved)
        env_secrets._captured = ''
        self.environ = {env_secrets.GEMINI_IMAGE_ENV: SECRET}
        env_secrets.capture(self.environ)
        self.records = []

        class Handler(logging.Handler):
            def emit(handler, record):
                self.records.append(record.getMessage() + ' ' + repr(record.args) + ' ' + str(record.exc_info))

        self.handler = Handler(level=logging.DEBUG)
        logging.getLogger().addHandler(self.handler)
        self.old_level = logging.getLogger().level
        logging.getLogger().setLevel(logging.DEBUG)
        self.addCleanup(logging.getLogger().removeHandler, self.handler)
        self.addCleanup(logging.getLogger().setLevel, self.old_level)

    def transport_fixture(self):
        seen, models = [], ('image-test', 'review-test')

        def transport(url, payload, headers, timeout, *, method='POST'):
            seen.append(dict(headers))
            if url.endswith('/v1beta/models'):
                return {'models': [{'name': 'models/' + m, 'supportedGenerationMethods': ['generateContent']}
                                   for m in models]}
            if url.endswith(':countTokens'):
                return {'totalTokens': 100}
            if '/image-test:' in url:
                part = {'inlineData': {'mimeType': 'image/png',
                                       'data': base64.b64encode(fixture_image(len(seen))).decode()}}
                return envelope([part])
            verdict = dict.fromkeys(('technical', 'anatomy', 'topic', 'brand', 'distinct'), True)
            return envelope([{'text': json.dumps({'scores': verdict, 'reason': 'fixture review'})}])
        return transport, seen

    def test_env_source_uses_the_captured_key_and_the_variable_is_gone(self):
        self.assertEqual(self.environ, {})
        transport, seen = self.transport_fixture()
        provider = GeminiImages(make_config(self.temp.name, key_source='env'), transport=transport)
        self.assertEqual(provider.headers()['x-goog-api-key'], SECRET)
        provider.verify_models()
        self.assertEqual(seen[-1]['x-goog-api-key'], SECRET)

    def test_file_source_is_unchanged_and_never_reads_the_env_capture(self):
        provider = GeminiImages(make_config(self.temp.name))
        self.assertIs(provider.key_reader.__name__ == 'read_key', True)
        self.assertEqual(provider.key_reader.__module__, 'nailblog.ai_transport')

    def test_missing_env_key_fails_closed_without_leaking(self):
        env_secrets._captured = ''
        provider = GeminiImages(make_config(self.temp.name, key_source='env'), transport=lambda *a, **k: {})
        with self.assertRaises(ImageError) as caught:
            provider.headers()
        self.assertEqual(str(caught.exception), 'key_required')

    def test_key_never_reaches_logs_exceptions_or_run_manifest(self):
        transport, seen = self.transport_fixture()
        config = make_config(self.temp.name, key_source='env')
        out = Path(self.temp.name) / 'out'
        pipeline = ImagePipeline(config, provider=GeminiImages(config, transport=transport), cover_renderer=fixture_cover)
        document = asyncio.run(pipeline.run(article(), out, 'run', 1, paid_enabled=True))
        self.assertTrue(seen and all(h['x-goog-api-key'] == SECRET for h in seen))
        self.assertTrue(json.loads((out / 'manifest.json').read_text(encoding='utf-8')))
        self.assertTrue(document)
        failing = GeminiImages(config, transport=lambda *a, **k: (_ for _ in ()).throw(ImageError('provider_failed')))
        with self.assertRaises(ImageError) as caught:
            failing.verify_models()
        self.assertNotIn(SECRET, repr(caught.exception))
        self.assertNotIn(SECRET, '\n'.join(self.records))
        for path in Path(self.temp.name).rglob('*'):
            if path.is_file():
                self.assertNotIn(SECRET.encode(), path.read_bytes(), str(path))
                self.assertNotIn(base64.b64encode(SECRET.encode()), path.read_bytes(), str(path))


class BindingDigestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.n = 0

    def recorded(self, **kw):
        """(config, binding, out dir): the binding the pipeline stores in paid_runs for a fresh DB; the provider
        stops the run right after the ledger accepted it."""
        self.n += 1
        base = Path(self.temp.name) / ('case%d' % self.n)
        base.mkdir()
        config = make_config(str(base), **kw)
        out = base / 'out'
        provider = CountingProvider(config)
        provider.verify_models = lambda: (_ for _ in ()).throw(ImageError('stop'))
        pipeline = ImagePipeline(config, provider=provider, cover_renderer=fixture_cover)
        with self.assertRaisesRegex(ImageError, 'stop'):
            asyncio.run(pipeline.run(article(), out, 'run', 1, paid_enabled=True))
        with pipeline.state.connection() as db:
            binding = db.execute('SELECT binding FROM paid_runs WHERE id=?', ('run',)).fetchone()[0]
        return config, binding, str(out.absolute())

    def pre_s0(self, config, directory, *, with_new_fields=False):
        """The digest exactly as built before key_source / image_unit_prices existed (or, with_new_fields, with the
        non-default new fields included, which is what a non-default config must produce)."""
        params = asdict(config.image)
        for name in ('key_file', 'review_bypass'):
            params.pop(name)
        if not with_new_fields or params['key_source'] == 'file':
            params.pop('key_source')
        prices = asdict(config.budget)
        if not with_new_fields or not prices['image_unit_prices']:
            prices.pop('image_unit_prices')
        return digest({'article': article(), 'image': params, 'prices': prices, 'processing': VERSION,
                       'review': REVIEW_VERSION, 'directory': directory,
                       'template': digest(files('nailblog.images').joinpath('templates/cover.html')
                                          .read_text(encoding='utf-8')),
                       'prompt_version': PROMPT_VERSION, 'cloudflare': None})

    def test_default_config_digest_equals_the_pre_s0_shape(self):
        config, binding, directory = self.recorded()
        self.assertEqual(config.image.key_source, 'file')
        self.assertEqual(config.budget.image_unit_prices, {})
        self.assertEqual(binding, self.pre_s0(config, directory))

    def test_non_default_values_change_the_digest(self):
        cases = (dict(key_source='env'),
                 dict(budget_extra={'image_unit_prices': {'image-test': {'1K': .05}}}),
                 dict(budget_extra={'image_unit_prices': {'image-test': {'1K': .06}}}))
        for kw in cases:
            with self.subTest(kw=kw):
                config, binding, directory = self.recorded(**kw)
                self.assertNotEqual(binding, self.pre_s0(config, directory))
                self.assertEqual(binding, self.pre_s0(config, directory, with_new_fields=True))


if __name__ == '__main__':
    unittest.main()
