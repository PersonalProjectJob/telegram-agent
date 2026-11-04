import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import test_official_auto_gate as fixture
from source_mirror.official_editor_adapter import plan
from source_mirror.official_sources import SOURCES,collect
from source_mirror.official_draft import create
from source_mirror.official_payload import build
from source_mirror.localization import MODEL,EFFORT

class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.base=fixture.AutomaticGateTests(); self.base.setUp(); self.addCleanup(self.base.doCleanups)

    def test_plan_rebuilds_payload_and_preserves_official_attribution(self):
        result=plan(self.base.draft,self.base.editorial,self.base.media,self.base.payload)
        self.assertEqual(result['status'],'CANARY_ONLY_NOT_PUBLISHABLE')
        self.assertEqual(result['payload_sha256'],hashlib.sha256(self.base.payload).hexdigest())
        self.assertEqual(result['source_label'],'CDC/NIOSH')
        self.assertIn('CDC/NIOSH',result['locales']['vi']['body_html'])
        self.assertNotIn('Scratch Magazine',result['locales']['vi']['body_html'])
        with self.assertRaises(ValueError): plan(self.base.draft,self.base.editorial,self.base.media,self.base.payload+b' ')

    def test_sba_and_tax_attribution_remain_source_specific(self):
        source=b'<html><head><title>Business planning</title></head><body><main><p>Business planning guidance.</p></main></body></html>'
        candidate={'title_vi':'Lập kế hoạch kinh doanh','text_vi':'Hướng dẫn lập kế hoạch kinh doanh.','uncertainties':[]}
        review={'verdict':'PASS','critical':0,'major':0,'minor':0,'issues':[],'uncertainties':[],'glossary_ok':True,'accuracy_ok':True,'technical_clarity_ok':True}
        cfg={'localization':{'enabled':True,'model':MODEL,'effort':EFFORT,'socket_path':'/unused'}}
        for source_id in ('sba-business-plan','irs-worker-classification'):
            item=next(x for x in SOURCES if x['id']==source_id); tax=item['category']=='us-tax'
            with self.subTest(source_id=source_id), tempfile.TemporaryDirectory() as root:
                collect(root,item,lambda _:source); values=iter([candidate,review])
                result=create(root,source_id,hashlib.sha256(source).hexdigest(),cfg,
                              lambda _: {'status':'SUCCESS','response':json.dumps(next(values),ensure_ascii=False)},allow_official_tax=tax)
                draft=Path(result['path']).read_bytes(); payload=build(draft,self.base.media,self.base.editorial,allow_official_tax=tax)
                body=plan(draft,self.base.editorial,self.base.media,payload)['locales']['en']['body_html']
                if tax:
                    self.assertIn('Internal Revenue Service (IRS)',body); self.assertIn('not individualized tax advice',body)
                    self.assertNotIn('Small Business Administration (SBA)',body)
                else:
                    self.assertIn('Small Business Administration (SBA)',body); self.assertIn('do not imply endorsement',body)
                    self.assertNotIn('individualized tax advice',body)
