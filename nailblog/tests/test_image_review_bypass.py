"""Behavioural contract of the temporary image.review_bypass switch (offline, fake providers only)."""
import copy
import json
import tempfile
import tomllib
import unittest
from dataclasses import replace
from pathlib import Path

from nailblog.admin_images import validate_files, verify_reviews
from nailblog.config import ContractConfig, ImageConfig, PRODUCTION_MEDIA_ORIGIN
from nailblog.images import ImageError
from nailblog.images.post import crop, webp
from nailblog.onepost import OnePost
from nailblog.publication import AdminError, digest
from nailblog.service import Service
from tests.fakes import make_article
from tests.helpers import load, settings
from tests.test_real_images import fixture_image

RUBRIC = ('technical', 'anatomy', 'topic', 'brand', 'distinct')
POSITIONS = ['cover', 'body-1', 'body-2', 'body-3']
EXAMPLE = Path(__file__).resolve().parents[2] / 'config' / 'nailblog.example.toml'
REJECTED = (AdminError, ImageError)


def with_bypass(c, flag, **image):
    return replace(c, image=replace(c.image, review_bypass=flag, **image))


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
