import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from source_mirror.official_sources import collect, SOURCES
from source_mirror.official_draft import create
from source_mirror.official_payload import build
from source_mirror.official_auto_gate import assess
from source_mirror.agy_broker import MODEL, EFFORT

HTML=b'<html><head><title>Safety</title></head><body><main><p>Safety guidance.</p></main></body></html>'


class AutomaticGateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve(); self.sha=hashlib.sha256(HTML).hexdigest()
        collect(self.root,SOURCES[0],lambda _:HTML)
        candidate={'title_vi':'An toàn làm việc','text_vi':'Hướng dẫn an toàn.','uncertainties':[]}
        review={'verdict':'PASS','critical':0,'major':0,'minor':0,'issues':[],'uncertainties':[],
                'glossary_ok':True,'accuracy_ok':True,'technical_clarity_ok':True}
        outputs=iter([candidate,review])
        cfg={'localization':{'enabled':True,'model':MODEL,'effort':EFFORT,'socket_path':'/not-used'}}
        result=create(self.root,SOURCES[0]['id'],self.sha,cfg,lambda _: {'status':'SUCCESS','response':json.dumps(next(outputs),ensure_ascii=False)})
        self.draft=Path(result['path']).read_bytes()
        self.media=[{'id':'figure','sha256':'a'*64,'provenance_url':'https://www.cdc.gov/report.pdf','credit':'NIOSH',
                     'rights_basis':'not verified','rights_evidence_sha256':'b'*64,'alt':'Figure'}]
        self.editorial={'locales':{'vi':{'summary':'An toàn','meta_title':'An toàn','meta_description':'Hướng dẫn'},
                                   'en':{'summary':'Safety','meta_title':'Safety','meta_description':'Guidance'}},
                        'hashtags':['nail','safety','work'],'category':'Safety','status':'Published','is_public':True,'is_featured':False,'video_urls':[],
                        'published_at':'2026-11-01T08:00:00+07:00'}
        self.payload=build(self.draft,self.media,self.editorial)

    def assess(self, **kwargs):
        return assess(self.root,SOURCES[0]['id'],self.sha,self.draft,self.payload,self.editorial,self.media,**kwargs)

    def test_consistent_draft_still_holds_when_real_protections_missing(self):
        result=self.assess()
        self.assertEqual(result['status'],'HOLD')
        self.assertIn('media_rights_verification_not_implemented',result['blockers'])
        self.assertIn('editorial_factual_and_geographic_verification_not_implemented',result['blockers'])
        self.assertIn('native_official_publication_contract_not_verified',result['blockers'])

    def test_stale_or_future_source_and_changed_payload_or_snapshot(self):
        self.assertIn('source_snapshot_outside_24_hour_window',self.assess(now=datetime.now(timezone.utc)+timedelta(days=2))['blockers'])
        self.assertIn('source_snapshot_outside_24_hour_window',self.assess(now=datetime.now(timezone.utc)-timedelta(days=2))['blockers'])
        self.payload=b'changed'
        with self.assertRaisesRegex(ValueError,'payload changed'): self.assess()
        path=self.root/'official-review-queue'/SOURCES[0]['id']/self.sha/'reference.txt'
        path.write_text('altered',encoding='utf-8')
        with self.assertRaises(ValueError): self.assess()


if __name__=='__main__': unittest.main()
