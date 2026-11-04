import copy
import hashlib
import json
import unittest
from unittest.mock import patch
from source_mirror.official_upload_controller import UploadObserver
from source_mirror.official_visual_evidence import inspect

class VisualEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.image=b'original';self.output=b'reencoded';self.sha='a'*64;self.source_sha='b'*64
        self.folder='/tmp/official-auto-evidence/sba-business-plan/'+self.source_sha+'/'+self.sha
        self.value={'source':{'id':'sba-business-plan'},'media':[{'id':'figure','sha256':hashlib.sha256(self.image).hexdigest()}]}
        self.payload=json.dumps(self.value).encode();self.media_policy={'version':'fixture'}
        self.live=(self.payload,{}, {},{'figure':self.image},self.media_policy)
        self.obs=object.__new__(UploadObserver);self.obs.closed=True
        self.obs.observation={'status':'CANARY_ONLY_NOT_PUBLISHABLE','record_sha256':self.sha,'media_id':'figure',
            'binding':{'source_sha256':self.value['media'][0]['sha256'],'response':{'imageUrl':'https://vlink-storages-prod.sgp1.cdn.digitaloceanspaces.com/a.jpg','thumbnailUrl':'','thumbnailRelativePath':''}}}
        self.policy={'version':'official-image-comparison-policy-v1','entries':[{'source_id':'sba-business-plan','source_sha256':self.source_sha,
             'image_sha256':self.value['media'][0]['sha256'],'mode':'general','max_rms':1,'max_channel_delta':16,
             'max_aspect_error':0.001,'min_scale':0.3,'resample':'lanczos','valid_until_utc':'2030-01-01T00:00:00Z'}]}
    def test_visual_check_is_not_publication_permission(self):
        review={'status':'VISUAL_QA_NOT_PUBLISHABLE'}
        with patch('source_mirror.official_visual_evidence._live_inputs',return_value=self.live),patch('source_mirror.official_visual_evidence.load_policy',return_value=self.policy), \
             patch('source_mirror.official_visual_evidence.readback',return_value=self.output),patch('source_mirror.official_visual_evidence.compare',return_value=review):
            result=inspect('/tmp',self.folder,self.sha,self.obs)
        self.assertFalse(result['publication_enabled']);self.assertEqual(result['output_sha256'],hashlib.sha256(self.output).hexdigest())
    def test_changed_output_or_incomplete_thumbnail_hold(self):
        with patch('source_mirror.official_visual_evidence._live_inputs',return_value=self.live),patch('source_mirror.official_visual_evidence.load_policy',return_value=self.policy), \
             patch('source_mirror.official_visual_evidence.readback',side_effect=[self.output,b'changed']),patch('source_mirror.official_visual_evidence.compare',return_value={'status':'VISUAL_QA_NOT_PUBLISHABLE'}):
            with self.assertRaisesRegex(ValueError,'output changed'):inspect('/tmp',self.folder,self.sha,self.obs)
        self.obs.observation['binding']['response']['thumbnailRelativePath']='/missing'
        with patch('source_mirror.official_visual_evidence._live_inputs',return_value=self.live),patch('source_mirror.official_visual_evidence.load_policy',return_value=self.policy), \
             patch('source_mirror.official_visual_evidence.readback',return_value=self.output),patch('source_mirror.official_visual_evidence.compare',return_value={'status':'VISUAL_QA_NOT_PUBLISHABLE'}):
            with self.assertRaisesRegex(ValueError,'incomplete thumbnail'):inspect('/tmp',self.folder,self.sha,self.obs)
