import copy
import json
import unittest
import test_official_auto_gate as fixture
from source_mirror.official_payload import build
from source_mirror.official_stock_policy import (is_candidate,require_license,PEXELS_URL,PEXELS_LICENSE,
                                                  PEXELS_IMAGE_SHA,PEXELS_CREDIT,PEXELS_ALT)

class StockTests(unittest.TestCase):
    def setUp(self):
        self.base=fixture.AutomaticGateTests();self.base.setUp();self.addCleanup(self.base.doCleanups)
        self.media=copy.deepcopy(self.base.media)
        self.media[0].update(provenance_url=PEXELS_URL,sha256=PEXELS_IMAGE_SHA,credit=PEXELS_CREDIT,alt=PEXELS_ALT)
    def test_candidate_identity_and_separate_license_evidence(self):
        self.assertTrue(is_candidate(self.media[0])); require_license(self.media[0],{'rights_evidence_url':PEXELS_LICENSE})
        for change in ({'sha256':'f'*64},{'credit':'another creator'},{'alt':'safety proven'}):
            image=dict(self.media[0],**change)
            with self.subTest(change=change),self.assertRaises(ValueError):is_candidate(image)
        with self.assertRaises(ValueError):require_license(self.media[0],{'rights_evidence_url':'https://example.com/'})
    def test_payload_visibly_distinguishes_stock_and_credits_creator(self):
        payload=json.loads(build(self.base.draft,self.media,self.base.editorial))
        for locale in ('vi','en'):
            body=payload['locales'][locale]['body_html'];self.assertIn(PEXELS_CREDIT,body)
        self.assertIn('Ảnh stock minh họa chung',payload['locales']['vi']['body_html'])
        self.assertIn('General stock illustration',payload['locales']['en']['body_html'])
