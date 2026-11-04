import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import test_official_auto_eligibility as fixture
from source_mirror.official_auto_evidence import capture
from source_mirror.official_payload import build


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixture.EligibilityTests(); self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.base=self.fixture.base
        self.result=self.fixture.evaluate()

    def capture(self):
        media=self.base.media; payload=build(self.fixture.draft,media,self.base.editorial)
        with patch('source_mirror.official_auto_evidence.assess',return_value=self.result):
            return capture(self.base.root,self.fixture.item['id'],hashlib.sha256(self.fixture.source).hexdigest(),
                           self.fixture.draft,payload,self.base.editorial,media,{'figure':self.fixture.image},
                           {'figure':self.fixture.rights},self.fixture.cfg)

    def test_records_refetch_payload_policy_media_and_model_evidence(self):
        result=self.capture(); folder=Path(result['path'])
        self.assertIs(result['publication_enabled'],False)
        self.assertEqual(hashlib.sha256((folder/'record.json').read_bytes()).hexdigest(),result['record_sha256'])
        record=json.loads((folder/'record.json').read_text(encoding='utf-8'))
        self.assertEqual((folder/'refetched-source.html').read_bytes(),self.fixture.source)
        for name,digest in record['files'].items():
            self.assertEqual(hashlib.sha256((folder/name).read_bytes()).hexdigest(),digest)
        self.assertIn('factual_envelope',record); self.assertIn('media_policy',record)
        self.assertEqual(result,self.capture())
        (folder/'media'/'01').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'corrupted'): self.capture()


if __name__=='__main__': unittest.main()
