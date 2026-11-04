import json
from pathlib import Path
import unittest


class NioshEvidenceTests(unittest.TestCase):
    def test_dossier_keeps_source_scope_and_publication_hold_distinct(self):
        path=Path(__file__).resolve().parents[2]/'Docs'/'00-master'/'research'/'nailhub-source-mirror-noissue_niosh-figure-b1_261029.json'
        data=json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(data['status'],'VERIFIED_SOURCE_US_SCOPE_ONLY')
        self.assertEqual(data['rights']['scope'],'US only')
        self.assertIs(data['rights']['publication_enabled'],False)
        self.assertEqual(data['rights']['international_distribution'],'Not verified')
        source=data['source']
        self.assertEqual(source['figure'],'B1'); self.assertEqual(source['figure_pdf_page'],24)
        self.assertEqual(source['availability_pdf_page'],2); self.assertEqual(source['publication_date'],'2019-03')
        self.assertEqual(source['candidate_image_sha256'],'cc8564a2e4de59ed8013c0c7b3475eea79879b19382d31cb58cba0482d0e5cc3')
        self.assertIn('2019',source['candidate_caption']); self.assertNotIn('2015 bulletin',source['candidate_caption'])
        self.assertIn('report_sha256_reported_by_controller',source)


if __name__=='__main__': unittest.main()
