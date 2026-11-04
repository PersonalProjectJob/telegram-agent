import hashlib
import json
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo
from source_mirror.official_sources import SOURCES
from source_mirror.official_draft_stager import discover,stage_draft

class DraftStagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve()
        self.item=next(x for x in SOURCES if x['id']=='irs-tip-reporting');self.source_sha='a'*64
        self.draft=b'official draft';self.draft_sha=hashlib.sha256(self.draft).hexdigest()
        folder=self.root/'official-drafts'/self.item['id']/self.source_sha;folder.mkdir(parents=True)
        (folder/(self.draft_sha+'.json')).write_bytes(self.draft)
        self.image=b'image';self.rights=b'public rights';self.imgsha=hashlib.sha256(self.image).hexdigest();self.rsha=hashlib.sha256(self.rights).hexdigest()
        base=self.root/'official-inputs';(base/'media').mkdir(parents=True);(base/'rights').mkdir()
        (base/'media'/self.imgsha).write_bytes(self.image);(base/'rights'/(self.rsha+'.txt')).write_bytes(self.rights)
        self.entry={'source_id':self.item['id'],'source_url':self.item['url'],'geographic_scope':self.item['scope'],
                    'jurisdiction':'WORLDWIDE','valid_until_utc':'2099-01-01T00:00:00Z','image_sha256':self.imgsha,
                    'rights_evidence_sha256':self.rsha,'provenance_url':'https://pexels.com/photo','credit':'credit','rights_basis':'license','alt':'stock'}
        self.policy={'entries':[self.entry],'category_mapping':{'Safety':'id'}}
        clock=patch('source_mirror.official_draft_stager.datetime',wraps=datetime);self.clock=clock.start();self.addCleanup(clock.stop)
        self.clock.now.side_effect=lambda zone:datetime(2026,10,30,5,0,tzinfo=zone)
    def call(self):return stage_draft(self.root,self.item['id'],self.source_sha,self.draft_sha,'Safety','08:00')
    def test_discover_and_prepared_inputs_delegate_to_trusted_runner(self):
        self.assertEqual(discover(self.root),[{'source_id':self.item['id'],'source_sha256':self.source_sha,'draft_sha256':self.draft_sha}])
        def run(root,name,**kwargs):
            data=json.loads((root/'official-inputs'/name).read_text(encoding='utf-8'))
            self.assertEqual(data['source_id'],self.item['id']);self.assertEqual(data['media'][0]['descriptor']['sha256'],self.imgsha)
            self.assertEqual(json.loads((root/'official-inputs'/data['editorial']).read_text())['published_at'],'2026-10-30T08:00:00+07:00')
            return {'status':'STAGED_NOT_PUBLISHABLE'}
        with patch('source_mirror.official_draft_stager.load_media_policy',return_value=self.policy),patch('source_mirror.official_draft_stager.is_candidate',return_value=True), \
             patch('source_mirror.official_draft_stager.require_license'),patch('source_mirror.official_draft_stager.run',side_effect=run):
            self.assertEqual(self.call()['status'],'STAGED_NOT_PUBLISHABLE')
        self.assertFalse(list((self.root/'official-inputs').glob('.official-stage-*')))
    def test_changed_media_wrong_scope_and_bad_slot_hold(self):
        with patch('source_mirror.official_draft_stager.load_media_policy',return_value=self.policy),patch('source_mirror.official_draft_stager.is_candidate',return_value=True), \
             patch('source_mirror.official_draft_stager.require_license'),patch('source_mirror.official_draft_stager.run') as run:
            (self.root/'official-inputs'/'media'/self.imgsha).write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'image changed'):self.call()
            (self.root/'official-inputs'/'media'/self.imgsha).write_bytes(self.image)
            self.entry['jurisdiction']='US'
            with self.assertRaisesRegex(ValueError,'worldwide'):self.call()
            self.entry['jurisdiction']='WORLDWIDE'
            with self.assertRaisesRegex(ValueError,'next scheduled'):stage_draft(self.root,self.item['id'],self.source_sha,self.draft_sha,'Safety','12:00')
            run.assert_not_called()
    def test_after_evening_slot_targets_next_morning(self):
        self.clock.now.side_effect=lambda zone:datetime(2026,10,30,21,10,tzinfo=zone)
        def run(root,name,**kwargs):
            manifest=json.loads((root/'official-inputs'/name).read_text(encoding='utf-8'))
            editorial=json.loads((root/'official-inputs'/manifest['editorial']).read_text())
            self.assertEqual(editorial['published_at'],'2026-10-31T08:00:00+07:00')
            return {'status':'STAGED_NOT_PUBLISHABLE'}
        with patch('source_mirror.official_draft_stager.load_media_policy',return_value=self.policy),patch('source_mirror.official_draft_stager.is_candidate',return_value=True), \
             patch('source_mirror.official_draft_stager.require_license'),patch('source_mirror.official_draft_stager.run',side_effect=run):
            self.assertEqual(self.call()['status'],'STAGED_NOT_PUBLISHABLE')
