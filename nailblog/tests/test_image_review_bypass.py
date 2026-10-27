"""Behavioural contract of the temporary image.review_bypass switch (offline, fake providers only)."""
import copy
import json
import tempfile
import tomllib
import unittest
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from nailblog.admin_images import validate_files, verify_reviews
from nailblog.config import ContractConfig, ImageConfig, PRODUCTION_MEDIA_ORIGIN
from nailblog.images import ImageError
from nailblog.images.cloudflare import GENERATION, REVIEW
from nailblog.images.pipeline import ImagePipeline
from nailblog.images.post import crop, hashes, webp
from nailblog.onepost import OnePost
from nailblog.publication import AdminError, digest
from nailblog.service import Service
from tests.fakes import make_article
from tests.helpers import load, settings
from tests.test_cloudflare_images import NOW, FixtureCloudflare, config as cloudflare_config, proof
from tests.test_real_images import article, envelope, fixture_cover, fixture_image

RUBRIC = ('technical', 'anatomy', 'topic', 'brand', 'distinct')
POSITIONS = ['cover', 'body-1', 'body-2', 'body-3']
EXAMPLE = Path(__file__).resolve().parents[2] / 'config' / 'nailblog.example.toml'
REJECTED = (AdminError, ImageError)


def with_bypass(c, flag, **image):
    return replace(c, image=replace(c.image, review_bypass=flag, **image))


class RejectingCloudflare(FixtureCloudflare):
    """Same fake as the shipped Cloudflare fixture, except the reviewer fails every rubric key."""
    def complete(self, model, body):
        if model == GENERATION:
            return super().complete(model, body)
        self.calls.append((model, body))
        value = envelope([{'text': json.dumps({'scores': dict.fromkeys(RUBRIC, False), 'reason': 'fixture rejection'})}])
        value.pop('usageMetadata')
        value['_cloudflare'] = {'usage': {'prompt_tokens': 1000, 'completion_tokens': 100}}
        return value


class ConfigTests(unittest.TestCase):
    def test_default_is_off(self):
        self.assertIs(ImageConfig().review_bypass, False)
        with tempfile.TemporaryDirectory() as d:
            self.assertIs(load(d).image.review_bypass, False)

    def test_toml_true_and_false_load_as_bool(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIs(load(d, image='review_bypass = true').image.review_bypass, True)
            self.assertIs(load(d, image='review_bypass = false').image.review_bypass, False)

    def test_non_bool_value_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            for body in ('review_bypass = "true"', 'review_bypass = 1', 'review_bypass = 0', 'review_bypass = "yes"'):
                with self.subTest(body=body), self.assertRaises(ValueError):
                    load(d, image=body)

    def test_example_template_documents_the_switch_as_false(self):
        text = EXAMPLE.read_text(encoding='utf-8')
        table = tomllib.loads(text)['image']
        self.assertIn('review_bypass', table)
        self.assertIs(table['review_bypass'], False)
        line = next(l for l in text.splitlines() if l.strip().startswith('review_bypass'))
        self.assertIn('#', line)  # one-line comment on the same line


class PipelineBase(unittest.IsolatedAsyncioTestCase):
    bypass = True
    provider_class = FixtureCloudflare
    candidates = 1

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.c = with_bypass(cloudflare_config(self.tmp.name), self.bypass, candidates=self.candidates)
        self.now = NOW
        proof(self.c)
        self.provider = self.provider_class(self.c)
        self.out = Path(self.tmp.name) / 'output'
        self.pipeline = ImagePipeline(self.c, provider=self.provider, clock=lambda: self.now, cover_renderer=fixture_cover)
        patcher = patch('nailblog.images.pipeline.MODEL_LOCK.enter', return_value=nullcontext())
        patcher.start()
        self.addCleanup(patcher.stop)

    async def run_pipeline(self, pipeline=None):
        return await (pipeline or self.pipeline).run(article(), self.out, 'free-run', 9000, free_enabled=True)

    def manifest(self):
        return json.loads((self.out / 'manifest.json').read_text(encoding='utf-8'))

    def models_called(self):
        return [model for model, _ in self.provider.calls]


class BypassPipelineTests(PipelineBase):
    async def test_no_reviewer_call_and_bypass_audit_everywhere(self):
        document = await self.run_pipeline()
        self.assertEqual(self.models_called(), [GENERATION] * 4)
        self.assertNotIn(REVIEW, self.models_called())
        hashes_ = document['image_hashes']
        self.assertEqual(len(hashes_), 4)
        # document
        self.assertEqual(set(document['image_reviews']), set(hashes_))
        self.assertTrue(all(value is None for value in document['image_reviews'].values()))
        self.assertEqual(document['review_bypass'], {'enabled': True, 'hashes': hashes_})
        # manifest
        manifest = self.manifest()
        self.assertEqual(manifest['status'], 'complete_with_review_bypass')
        self.assertEqual(manifest['automatic_flow_verdict'], 'REVIEW_BYPASSED')
        self.assertEqual(manifest['review_bypass']['hashes'], hashes_)
        self.assertIs(manifest['review_bypass']['enabled'], True)
        self.assertEqual(manifest['review_bypass']['positions'], POSITIONS)
        self.assertEqual(manifest['image_hashes'], hashes_)
        self.assertEqual(manifest['document_digest'], digest(json.loads((self.out / 'document.json').read_text(encoding='utf-8'))))
        for position, sha in zip(POSITIONS, hashes_):
            entry = manifest['positions'][position]
            self.assertEqual(entry['status'], 'review_bypassed')
            self.assertEqual(entry['sha'], sha)
            self.assertIsNone(entry['scores'])
            self.assertIsNone(entry['review_intent'])
            self.assertTrue(entry['intent'])
        for name, entry in manifest['candidates'].items():
            self.assertEqual(entry['status'], 'review_bypassed', name)
            self.assertIsNone(entry['scores'])
            self.assertIsNone(entry['review_intent'])
            self.assertTrue(entry['intent'])

    async def test_ledger_holds_only_generation_and_no_review_hold_leaks(self):
        await self.run_pipeline()
        rows = self.pipeline.ledger.report('free-run')
        self.assertEqual(len(rows), 4)
        self.assertEqual({row['model'] for row in rows}, {GENERATION})
        self.assertTrue(all(row['status'] in ('settled', 'estimated') for row in rows))
        with self.pipeline.ledger.state.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cf_review_holds').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COALESCE(SUM(reserved),0) FROM cf_review_holds').fetchone()[0], 0)
        self.assertEqual(sum(row['reserved'] for row in rows),
                         sum(row['reserved'] for row in rows if row['model'] == GENERATION))

    async def test_each_position_takes_first_deterministically_passing_candidate(self):
        self.c = with_bypass(self.c, True, candidates=3)
        self.provider = FixtureCloudflare(self.c)
        self.pipeline = ImagePipeline(self.c, provider=self.provider, clock=lambda: self.now, cover_renderer=fixture_cover)
        document = await self.run_pipeline()
        self.assertEqual(len(self.provider.calls), 4)  # one generation per position, remaining candidates never requested
        manifest = self.manifest()
        for position in POSITIONS:
            self.assertEqual(manifest['positions'][position]['file'].rsplit('-', 2)[-2:], ['r0', 'c0.webp'])
        self.assertEqual(len(document['image_hashes']), 4)

    async def test_complete_bypass_resume_makes_zero_provider_calls_even_with_expired_proof(self):
        document = await self.run_pipeline()
        calls = len(self.provider.calls)
        self.now += 7200
        self.provider.complete = Mock(side_effect=AssertionError('provider called on resume'))
        self.assertEqual(await self.run_pipeline(), document)
        self.provider.complete.assert_not_called()
        self.assertEqual(len(self.provider.calls), calls)

    async def test_resume_with_switch_flipped_off_fails_closed(self):
        await self.run_pipeline()
        before = (self.out / 'manifest.json').read_bytes()
        off = with_bypass(self.c, False)
        provider = FixtureCloudflare(off)
        pipeline = ImagePipeline(off, provider=provider, clock=lambda: self.now, cover_renderer=fixture_cover)
        with self.assertRaises(ImageError):
            await self.run_pipeline(pipeline)
        self.assertEqual(provider.calls, [])
        self.assertEqual((self.out / 'manifest.json').read_bytes(), before)

    async def test_tampered_selected_bytes_block_bypass_resume(self):
        document = await self.run_pipeline()
        calls = len(self.provider.calls)
        Path(document['cover_path']).write_bytes(b'tampered')
        with self.assertRaises(Exception):
            await self.run_pipeline()
        self.assertEqual(len(self.provider.calls), calls)


class BypassDeterministicGateTests(PipelineBase):
    candidates = 2

    async def test_duplicate_of_history_is_still_rejected_and_next_candidate_is_used(self):
        raw = fixture_image(1, size=(1600, 960))
        _, dhash = hashes(webp(crop(raw, (1600, 900)), 200 * 1024))
        original = self.pipeline.historical_hashes  # real method keeps its side effects (image_selections table)
        self.pipeline.historical_hashes = lambda: [*original(), dhash]
        document = await self.run_pipeline()
        self.assertNotIn(REVIEW, self.models_called())
        self.assertEqual(len(self.provider.calls), 5)  # cover needed a second candidate, bodies one each
        manifest = self.manifest()
        first = manifest['candidates']['cover-r0-c0']
        self.assertEqual(first['status'], 'rejected')
        self.assertIn('duplicate', first['reason'])
        cover = manifest['positions']['cover']
        self.assertEqual(cover['status'], 'review_bypassed')
        self.assertTrue(cover['file'].endswith('cover-r0-c1.webp'))
        self.assertEqual(manifest['candidates']['cover-r0-c1']['status'], 'review_bypassed')
        self.assertEqual(manifest['status'], 'complete_with_review_bypass')
        self.assertEqual(len(document['image_hashes']), 4)
        with self.pipeline.ledger.state.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cf_review_holds').fetchone()[0], 0)

    async def test_oversized_image_is_still_rejected_by_size_gate(self):
        original = webp
        calls = []

        def big_then_normal(image, limit, *a, **k):
            calls.append(limit)
            if len(calls) == 1:
                raise ImageError('image_too_large')
            return original(image, limit, *a, **k)

        with patch('nailblog.images.pipeline.webp', side_effect=big_then_normal):
            document = await self.run_pipeline()
        manifest = self.manifest()
        self.assertEqual(manifest['candidates']['cover-r0-c0']['status'], 'rejected')
        self.assertEqual(manifest['positions']['cover']['status'], 'review_bypassed')
        self.assertTrue(manifest['positions']['cover']['file'].endswith('cover-r0-c1.webp'))
        self.assertNotIn(REVIEW, self.models_called())
        self.assertEqual(len(document['image_hashes']), 4)


class SwitchOffPipelineTests(PipelineBase):
    bypass = False

    async def test_reviewer_still_called_and_artifacts_have_no_bypass_markers(self):
        document = await self.run_pipeline()
        self.assertEqual(self.models_called().count(REVIEW), 4)
        self.assertEqual(self.models_called().count(GENERATION), 4)
        self.assertNotIn('review_bypass', document)
        self.assertTrue(all(isinstance(v, dict) and all(v.values()) for v in document['image_reviews'].values()))
        manifest = self.manifest()
        self.assertEqual(manifest['status'], 'complete')
        self.assertNotIn('review_bypass', manifest)
        self.assertNotEqual(manifest.get('automatic_flow_verdict'), 'REVIEW_BYPASSED')
        self.assertTrue(all(e['status'] == 'approved' and e['review_intent'] for e in manifest['positions'].values()))

    async def test_rejecting_reviewer_still_ends_in_quality_hold(self):
        self.provider = RejectingCloudflare(self.c)
        self.pipeline = ImagePipeline(self.c, provider=self.provider, clock=lambda: self.now, cover_renderer=fixture_cover)
        with self.assertRaisesRegex(ImageError, 'image_quality_held'):
            await self.run_pipeline()
        self.assertEqual(self.models_called(), [GENERATION, REVIEW])
        self.assertFalse((self.out / 'document.json').exists())
        self.assertNotEqual(self.manifest().get('status'), 'complete_with_review_bypass')


class VerifyReviewsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.hashes = [format(n, '064x') for n in range(1, 5)]
        self.on = with_bypass(settings(self.tmp.name), True)
        self.off = with_bypass(settings(self.tmp.name), False)
        self.document = {'image_hashes': list(self.hashes), 'image_reviews': dict.fromkeys(self.hashes),
                         'review_bypass': {'enabled': True, 'hashes': list(self.hashes)}}

    def test_exact_hash_list_accepted_only_when_switch_is_on(self):
        verify_reviews(self.document, self.hashes, config=self.on)

    def test_rejected_when_switch_is_off(self):
        with self.assertRaises(REJECTED):
            verify_reviews(self.document, self.hashes, config=self.off)
        with self.assertRaises(REJECTED):
            verify_reviews(self.document, self.hashes, config=None)

    def test_rejected_when_bypass_hashes_differ_or_are_reordered(self):
        for hashes_ in (self.hashes[::-1], self.hashes[:3], [*self.hashes[:3], '9' * 64], [*self.hashes, '9' * 64], []):
            with self.subTest(hashes=hashes_):
                document = {**self.document, 'review_bypass': {'enabled': True, 'hashes': hashes_}}
                with self.assertRaises(REJECTED):
                    verify_reviews(document, self.hashes, config=self.on)

    def test_rejected_when_enabled_flag_missing_or_review_value_present(self):
        with self.assertRaises(REJECTED):
            verify_reviews({**self.document, 'review_bypass': {'hashes': self.hashes}}, self.hashes, config=self.on)
        reviews = dict.fromkeys(self.hashes)
        reviews[self.hashes[0]] = dict.fromkeys(RUBRIC, True)
        with self.assertRaises(REJECTED):
            verify_reviews({**self.document, 'image_reviews': reviews}, self.hashes, config=self.on)
        reviews[self.hashes[0]] = dict.fromkeys(RUBRIC, False)
        with self.assertRaises(REJECTED):
            verify_reviews({**self.document, 'image_reviews': reviews}, self.hashes, config=self.on)

    def test_rejected_when_mixed_with_test_image_waiver(self):
        document = {**self.document, 'test_image_waiver': {'run_id': 'r', 'binding': 'b', 'entries': {}}}
        for config in (self.on, self.off):
            with self.assertRaises(REJECTED):
                verify_reviews(document, self.hashes, config=config)

    def test_hash_list_must_match_document_image_hashes(self):
        with self.assertRaises(REJECTED):
            verify_reviews(self.document, self.hashes[::-1], config=self.on)

    def test_regular_rubric_documents_unchanged_in_both_switch_states(self):
        document = {'image_hashes': list(self.hashes),
                    'image_reviews': {h: dict.fromkeys(RUBRIC, True) for h in self.hashes}}
        for config in (self.on, self.off):
            verify_reviews(document, self.hashes, config=config)
        document['image_reviews'][self.hashes[1]]['topic'] = False
        for config in (self.on, self.off):
            with self.assertRaises(REJECTED):
                verify_reviews(document, self.hashes, config=config)
        # None reviews without a bypass block are never a pass.
        with self.assertRaises(REJECTED):
            verify_reviews({'image_hashes': list(self.hashes), 'image_reviews': dict.fromkeys(self.hashes)},
                           self.hashes, config=self.on)


class OnePostBypassTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = settings(self.temp.name, mode='live',
                             contract=ContractConfig(verified=True, media_origin=PRODUCTION_MEDIA_ORIGIN))
        self.config = with_bypass(self.base, True)
        self.service = Service(self.config, clock=lambda: 1792458000)
        self.service.state.set_paused(False)
        self.service.save_cache([], [])
        self.topic = self.service.state.add_topic('2026-W43', 'Màu nail merlot', 'nail-salon',
                                                  '2026-10-25T19:00+07:00', state='approved')
        self.workflow = OnePost(self.service)
        self.article = make_article()
        self.directory = self.root / 'accepted'
        self.directory.mkdir()
        paths = []
        for i in range(4):
            path = self.directory / ('cover.webp' if i == 0 else f'body-{i}.webp')
            path.write_bytes(webp(crop(fixture_image(i + 70), (1600, 900) if i == 0 else (1200, 675)),
                                  200 * 1024 if i == 0 else 150 * 1024))
            paths.append(str(path))
        hashes_ = validate_files(paths[0], paths[1:])
        metadata = [{'src': Path(p).name, 'alt': slot['image_slot']['alt'], 'caption': slot['image_slot'].get('caption', ''),
                     'width': 1200, 'height': 675}
                    for p, slot in zip(paths[1:], [s for s in self.article['sections'] if s.get('image_slot')])]
        self.document = {'article': self.article, 'cover_path': paths[0], 'image_paths': paths[1:],
                         'image_hashes': hashes_, 'image_reviews': dict.fromkeys(hashes_),
                         'review_bypass': {'enabled': True, 'hashes': list(hashes_)}, 'images': metadata}
        self.document_file = self.directory / 'document.json'
        self.write()

    def write(self, *, status='complete_with_review_bypass', entry_status='review_bypassed', audit=True):
        hashes_ = self.document['image_hashes']
        self.document_file.write_text(json.dumps(self.document), encoding='utf-8')
        entries = {role: {'status': entry_status, 'sha': sha, 'intent': 'fixture-generated-' + role,
                          'review_intent': None, 'scores': None,
                          'review_bypass': {'config_flag': 'image.review_bypass', 'rubric_skipped': 'any'}}
                   for role, sha in zip(POSITIONS, hashes_)}
        manifest = {'status': status, 'positions': entries, 'document_digest': digest(self.document),
                    'automatic_flow_verdict': 'REVIEW_BYPASSED'}
        if audit:
            manifest['review_bypass'] = {'enabled': True, 'positions': list(POSITIONS), 'hashes': list(hashes_)}
        (self.directory / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')

    async def prepare(self, workflow=None):
        return await (workflow or self.workflow).prepare('one', self.topic, self.root / 'out', article=self.article,
                                                         images_document=str(self.document_file))

    def provenance(self, config):
        return OnePost.provenance(self.document, self.directory, config=config)

    def test_provenance_accepts_bypassed_positions_when_switch_on(self):
        self.assertTrue(self.provenance(self.config))

    def test_provenance_rejects_same_document_when_switch_off(self):
        with self.assertRaises(REJECTED):
            self.provenance(with_bypass(self.base, False))

    def test_provenance_rejects_bypassed_positions_under_a_regular_complete_manifest(self):
        self.write(status='complete')
        with self.assertRaises(REJECTED):
            self.provenance(self.config)

    def test_provenance_rejects_when_manifest_audit_missing_or_different(self):
        self.write(audit=False)
        with self.assertRaises(REJECTED):
            self.provenance(self.config)
        self.write()
        manifest_path = self.directory / 'manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        manifest['review_bypass']['hashes'] = manifest['review_bypass']['hashes'][::-1]
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaises(REJECTED):
            self.provenance(self.config)

    def test_provenance_rejects_missing_or_wrong_position_bypass_record(self):
        manifest_path = self.directory / 'manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        for change in ('missing', 'wrong_flag'):
            with self.subTest(change=change):
                broken = copy.deepcopy(manifest)
                if change == 'missing':
                    del broken['positions']['body-1']['review_bypass']
                else:
                    broken['positions']['body-1']['review_bypass']['config_flag'] = 'other.flag'
                manifest_path.write_text(json.dumps(broken), encoding='utf-8')
                with self.assertRaises(REJECTED):
                    self.provenance(self.config)

    def test_provenance_rejects_missing_generation_intent_or_wrong_sha(self):
        self.write()
        manifest_path = self.directory / 'manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        broken = copy.deepcopy(manifest)
        broken['positions']['body-1']['intent'] = ''
        manifest_path.write_text(json.dumps(broken), encoding='utf-8')
        with self.assertRaises(REJECTED):
            self.provenance(self.config)
        broken = copy.deepcopy(manifest)
        broken['positions']['body-2']['sha'] = '0' * 64
        manifest_path.write_text(json.dumps(broken), encoding='utf-8')
        with self.assertRaises(REJECTED):
            self.provenance(self.config)

    def test_regular_reviewed_provenance_is_unchanged(self):
        hashes_ = self.document['image_hashes']
        scores = dict.fromkeys(RUBRIC, True)
        regular = {key: value for key, value in self.document.items() if key != 'review_bypass'}
        regular['image_reviews'] = {h: scores for h in hashes_}
        self.document_file.write_text(json.dumps(regular), encoding='utf-8')
        entries = {role: {'status': 'approved', 'sha': sha, 'intent': 'g-' + role, 'review_intent': 'r-' + role,
                          'scores': scores} for role, sha in zip(POSITIONS, hashes_)}
        (self.directory / 'manifest.json').write_text(
            json.dumps({'status': 'complete', 'positions': entries, 'document_digest': digest(regular)}), encoding='utf-8')
        for flag in (True, False):
            self.assertTrue(OnePost.provenance(regular, self.directory, config=with_bypass(self.base, flag)))

    async def test_prepare_exposes_review_bypass_in_approval_artifact_when_switch_on(self):
        first = await self.prepare()
        self.assertEqual(first['phase'], 'images_ready')
        self.assertEqual(first['review_bypass'], {'enabled': True, 'hashes': self.document['image_hashes']})
        stored = self.workflow.artifact('one')
        self.assertEqual(stored['review_bypass'], first['review_bypass'])
        # The Owner's snapshot covers the bypass block: accepting with it works, a stale value does not.
        with self.assertRaisesRegex(AdminError, 'image_approval_snapshot_changed'):
            self.workflow.accept_images('one', 'wrong')
        self.assertEqual(self.workflow.accept_images('one', first['snapshot'])['phase'], 'images_approved')

    async def test_prepare_rejects_same_document_when_switch_off(self):
        off = OnePost(Service(with_bypass(self.base, False), clock=lambda: 1792458000))
        with self.assertRaises(REJECTED):
            await self.prepare(off)
        self.assertIsNone(off.read('one'))


if __name__ == '__main__':
    unittest.main()
