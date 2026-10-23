"""S3: `image-compare` with a fake Gemini transport and the real PaidLedger on a temporary SQLite DB."""
import base64
import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from nailblog.__main__ import main
from nailblog.config import BudgetConfig, ImageConfig
from nailblog.images import ImageError
from nailblog.images.compare import parse_variants, run_compare
from nailblog.paid import PaidLedger
from nailblog.schedule import VN_TZ
from nailblog.state import State
from tests.helpers import settings
from tests.test_real_images import article, fixture_image

SECRET = 'AIza-compare-secret-key-0123456789'
MODELS = ('img-a', 'img-b', 'img-c', 'img-d', 'img-small')


class FakeGemini:
    """Transport with the provider's signature; records every call and never touches the network."""
    def __init__(self, sizes=None, listed=MODELS, scripted=()):
        self.scripted = list(scripted)   # one entry per generateContent call: None = normal, else a mutator/raiser
        self.calls, self.sizes, self.listed, self.headers_seen, self.prompts = [], sizes or {}, listed, [], []

    def __call__(self, url, payload, headers, timeout, *, method='POST'):
        self.calls.append((method, url))
        self.headers_seen.append(headers)
        if method == 'GET':
            return {'models': [{'name': 'models/' + name, 'supportedGenerationMethods': ['generateContent']}
                               for name in self.listed]}
        self.prompts.append(payload['contents'][0]['parts'][0]['text'])
        model = url.rsplit('/', 1)[1].split(':')[0]
        size = self.sizes.get(model, (1700, 1000))
        data = base64.b64encode(fixture_image(len(self.calls), size)).decode()
        envelope = {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [
                    {'thought': True, 'text': 'thinking'}, {'inlineData': {'mimeType': 'image/png', 'data': data}}]}}],
                'usageMetadata': {'promptTokenCount': 100, 'candidatesTokenCount': 90, 'thoughtsTokenCount': 10}}
        step = self.scripted.pop(0) if self.scripted else None
        return step(envelope) if step else envelope

    def generate_models(self):
        return [url.rsplit('/', 1)[1].split(':')[0] for method, url in self.calls if method == 'POST']

    def list_calls(self):
        return [url for method, url in self.calls if method == 'GET']


class CompareTests(unittest.TestCase):
    T0 = datetime(2026, 10, 23, 12, 0, tzinfo=VN_TZ).timestamp()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name) / 'board'

    def config(self, daily=3.0, unit=0.04):
        budget = BudgetConfig(daily, {name: (.5, 3) for name in MODELS}, {}, {name: 3 for name in MODELS},
                              {name: {'1K': unit, '2K': unit} for name in MODELS})
        return settings(self.temp.name, image=ImageConfig(model_body='img-a', model_cover='img-a', review_model='img-a'),
                        budget=budget)

    def run_it(self, config, variants, max_usd, transport):
        return run_compare(config, article(), self.out, parse_variants(variants), max_usd, paid_enabled=True,
                           clock=lambda: self.T0, transport=transport, key_reader=lambda *a, **k: SECRET)

    def settled_sum(self, config):
        with State(config.db_path).connection() as db:
            return db.execute("SELECT COALESCE(SUM(charged),0) FROM paid_intents WHERE status='settled'").fetchone()[0] / 1e6

    def test_board_with_costs_matches_ledger_and_hides_key(self):
        config, fake = self.config(), FakeGemini()
        with self.assertLogs('nailblog', level='INFO') as logs:
            board = self.run_it(config, 'img-a@2K,img-b@1K', 1.0, fake)
        self.assertEqual(['img-a', 'img-a', 'img-b', 'img-b'], fake.generate_models())
        self.assertEqual(4, len(board['results']))
        for result in board['results']:
            self.assertEqual('ok', result['status'])
            self.assertEqual(0.04, result['usd'])
            self.assertEqual([1700, 1000], result['pixels'])
            self.assertTrue(result['crop_ok'])
            self.assertEqual({'prompt': 100, 'candidates': 90, 'thoughts': 10}, result['tokens'])
        self.assertEqual(0.16, board['total_usd'])
        self.assertEqual(self.settled_sum(config), board['ledger_settled_usd'])
        self.assertEqual(board['total_usd'], board['ledger_settled_usd'])
        text = (self.out / 'board.html').read_text(encoding='utf-8')
        self.assertIn('data:image/png;base64,', text)
        saved = json.loads((self.out / 'board.json').read_text(encoding='utf-8'))
        self.assertEqual(board, saved)
        self.assertNotIn('data:image', json.dumps(saved))
        for blob in (text, json.dumps(saved), '\n'.join(logs.output)):
            self.assertNotIn(SECRET, blob)
        self.assertTrue(all(h['x-goog-api-key'] == SECRET for h in fake.headers_seen))  # the key was really sent
        if os.name == 'posix':
            self.assertEqual(0o600, (self.out / 'board.html').stat().st_mode & 0o777)
            self.assertEqual(0o600, (self.out / 'board.json').stat().st_mode & 0o777)

    def test_prompts_are_the_production_prompts(self):
        from nailblog.images.pipeline import slots
        from nailblog.images.prompts import generation_prompt
        fake = FakeGemini()
        self.run_it(self.config(), 'img-a@1K', 1.0, fake)
        positions = slots(article())
        self.assertEqual([generation_prompt(positions[i]['brief'], i, positions[i]['subject']) for i in (0, 1)],
                         fake.prompts)

    def test_max_usd_stops_extra_variants_before_any_call(self):
        fake = FakeGemini()
        board = self.run_it(self.config(), 'img-a@1K,img-b@1K,img-c@1K', 0.09, fake)
        self.assertEqual(['img-a', 'img-a'], fake.generate_models())
        self.assertEqual(1, len(fake.list_calls()))  # no ListModels for stopped variants
        status = {(r['model'], r['role']): r['status'] for r in board['results']}
        self.assertEqual('ok', status[('img-a', 'body')])
        self.assertEqual({'blocked'}, {s for (m, _), s in status.items() if m != 'img-a'})
        self.assertEqual(0.08, board['total_usd'])
        self.assertEqual(0.08, board['ledger_settled_usd'])

    def test_daily_cap_stops_before_call_and_keeps_finished(self):
        fake = FakeGemini()
        board = self.run_it(self.config(daily=0.05), 'img-a@1K,img-b@1K', 100.0, fake)
        self.assertEqual(['img-a'], fake.generate_models())
        self.assertEqual(0.05, board['run_ceiling_usd'])
        self.assertEqual(['ok', 'blocked', 'blocked', 'blocked'], [r['status'] for r in board['results']])
        self.assertEqual('max_usd_would_be_exceeded', board['results'][1]['reason'])  # run ceiling == what the day had left
        self.assertEqual(0.04, board['ledger_settled_usd'])

    def test_daily_cap_counts_spend_already_booked_today(self):
        config = self.config(daily=0.05)
        State(config.db_path).add_usage(self.T0 - 60, 'gemini', 'img-a', 1, 0.03)
        fake = FakeGemini()
        board = self.run_it(config, 'img-a@1K', 1.0, fake)
        self.assertEqual([], fake.generate_models())
        self.assertEqual([], fake.calls)
        self.assertEqual({'blocked'}, {r['status'] for r in board['results']})

    def test_daily_cap_reached_by_other_reservation_after_run_start(self):
        # A reservation by another process lands after the ceiling was computed: the per-image daily check blocks it.
        config = self.config(daily=0.1)
        fake = FakeGemini()
        original = fake.__call__
        def intrude(url, payload, headers, timeout, *, method='POST'):
            result = original(url, payload, headers, timeout, method=method)
            if method == 'POST' and len(fake.generate_models()) == 1:
                other = PaidLedger(State(config.db_path), config, lambda: self.T0)
                other.run('other-run', 'b', 1.0)
                other.reserve('other-intent', 'gemini', 'img-b', 0.05, run_id='other-run')
            return result
        board = self.run_it(config, 'img-a@1K', 1.0, intrude)
        self.assertEqual(['ok', 'blocked'], [r['status'] for r in board['results']])
        self.assertEqual('daily_cap_would_be_exceeded', board['results'][1]['reason'])
        self.assertEqual(1, len(fake.generate_models()))

    def test_undersized_output_is_fail_and_never_upscaled(self):
        fake = FakeGemini(sizes={'img-small': (1000, 560)})
        board = self.run_it(self.config(), 'img-small@1K', 1.0, fake)
        cover, body = board['results']
        self.assertEqual('fail_crop', cover['status'])
        self.assertEqual([1000, 560], cover['pixels'])
        self.assertFalse(cover['crop_ok'])
        self.assertEqual(0.04, cover['usd'])  # the call was paid and is still on the board
        self.assertEqual('fail_crop', body['status'])  # 1000x560 < 1200x675
        text = (self.out / 'board.html').read_text(encoding='utf-8')
        self.assertIn('FAIL', text)
        self.assertIn('width="1000" height="560"', text)
        self.assertNotIn('width="1600"', text)

    def test_stale_pending_and_old_uncertain_are_recovered_before_reserving(self):
        config = self.config()
        old = PaidLedger(State(config.db_path), config, lambda: self.T0 - 5000)
        old.run('crashed-run', 'binding', 1.0)
        old.reserve('stale-pending', 'gemini', 'img-a', 0.1, run_id='crashed-run')
        ancient = PaidLedger(State(config.db_path), config, lambda: self.T0 - 3 * 86400)
        ancient.run('ancient-run', 'binding', 1.0)
        ancient.reserve('ancient-uncertain', 'gemini', 'img-a', 0.1, run_id='ancient-run')
        ancient.uncertain('ancient-uncertain')
        self.run_it(config, 'img-a@1K', 1.0, FakeGemini())
        ledger = PaidLedger(State(config.db_path), config, lambda: self.T0)
        self.assertEqual('uncertain', ledger.get('stale-pending')['status'])
        settled = ledger.get('ancient-uncertain')
        self.assertEqual(('settled', 100000), (settled['status'], settled['charged']))
        self.assertIn('assumed_full_bound', settled['metadata'])

    def uncertain_case(self, step, reason):
        from nailblog.images.compare import _daily_left_micros
        config, fake = self.config(), FakeGemini(scripted=[step])
        board = self.run_it(config, 'img-a@1K', 0.09, fake)
        cover, body = board['results']
        self.assertEqual(('error', reason, 0.0), (cover['status'], cover['reason'], cover['usd']))
        ledger = PaidLedger(State(config.db_path), config, lambda: self.T0)
        row = ledger.get(board['run_id'] + '-v0-cover')
        self.assertEqual(('uncertain', 0, 40000), (row['status'], row['charged'], row['reserved']))
        # the unknown call keeps its full bound in the daily sum (3.00 - 0.04 settled body - 0.04 uncertain cover)
        self.assertEqual(2_920_000, _daily_left_micros(ledger, self.T0))
        # the code intends to continue: the next image is still affordable inside max_usd (0.04 + 0.04 <= 0.09)
        self.assertEqual('ok', body['status'])
        self.assertEqual(2, len(fake.generate_models()))
        self.assertEqual(0.04, board['ledger_settled_usd'])

    def test_timeout_after_dispatch_leaves_full_reservation_uncertain(self):
        def timeout(envelope):
            raise ImageError('timeout')
        self.uncertain_case(timeout, 'timeout')

    def test_truncated_generation_leaves_full_reservation_uncertain(self):
        def truncated(envelope):
            envelope['candidates'][0]['finishReason'] = 'MAX_TOKENS'
            return envelope
        self.uncertain_case(truncated, 'generation_refused_or_truncated')

    def test_reported_usage_above_input_ceiling_is_uncertain(self):
        def too_many(envelope):
            envelope['usageMetadata']['promptTokenCount'] = 4097
            return envelope
        self.uncertain_case(too_many, 'reported_usage_exceeds_reserved_bound')

    def test_same_second_runs_with_different_variants_get_distinct_run_ids(self):
        first = self.run_it(self.config(), 'img-a@1K', 1.0, FakeGemini())
        self.out = Path(self.temp.name) / 'board2'
        second = self.run_it(self.config(), 'img-b@1K', 1.0, FakeGemini())
        self.assertNotEqual(first['run_id'], second['run_id'])
        self.assertRegex(second['run_id'], r'^compare-\d{8}T\d{6}Z-[0-9a-f]{6}$')

    def test_second_run_on_same_out_is_refused_while_locked(self):
        from nailblog._runtime import AlreadyRunning, ProcessLock
        self.out.mkdir()
        fake = FakeGemini()
        with ProcessLock(self.out), self.assertRaises(AlreadyRunning):
            self.run_it(self.config(), 'img-a@1K', 1.0, fake)
        self.assertEqual([], fake.calls)

    def test_unlisted_model_makes_no_generation_call(self):
        fake = FakeGemini(listed=('img-b',))
        board = self.run_it(self.config(), 'img-a@1K', 1.0, fake)
        self.assertEqual([], fake.generate_models())
        self.assertEqual({'error'}, {r['status'] for r in board['results']})
        self.assertTrue(board['results'][0]['reason'].startswith('model_check_failed:model_not_available'))
        self.assertEqual(0.0, board['ledger_settled_usd'])

    def test_refuses_without_paid_enabled_and_makes_no_call(self):
        fake = FakeGemini()
        with self.assertRaises(ImageError):
            run_compare(self.config(), article(), self.out, parse_variants('img-a@1K'), 1.0, paid_enabled=False,
                        transport=fake, key_reader=lambda *a, **k: SECRET)
        self.assertEqual([], fake.calls)
        self.assertFalse(self.out.exists())

    def test_cli_refuses_without_paid_enabled(self):
        path = Path(self.temp.name) / 'a.json'
        path.write_text(json.dumps(article()), encoding='utf-8')
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as caught:
            main(['image-compare', '--config', str(Path(self.temp.name) / 'missing.toml'), '--article', str(path),
                  '--out', str(self.out), '--variants', 'img-a@1K', '--max-usd', '0.5'])
        self.assertEqual(2, caught.exception.code)
        self.assertIn('--paid-enabled', err.getvalue())
        self.assertFalse(self.out.exists())

    def test_parse_variants(self):
        self.assertEqual([('img-a', '2K'), ('img-b', '1K')], parse_variants('img-a@2K, img-b@1K'))
        for bad in ('', 'img-a', 'img-a@3K', 'img-a@1K,img-a@1K', '@1K'):
            with self.assertRaises(ImageError):
                parse_variants(bad)


if __name__ == '__main__':
    unittest.main()
