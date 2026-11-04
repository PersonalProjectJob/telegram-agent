import unittest
from pathlib import Path
from unittest.mock import patch
from source_mirror.official_scratch_publish import verify_media

class OfficialScratchMediaTests(unittest.TestCase):
    def test_empty_thumbnail_uses_verified_image_and_partial_thumbnail_holds(self):
        descriptor={'id':'m','sha256':'a'*64}
        payload={'source':{'id':'cdc'},'media':[descriptor]}
        response={'imageUrl':'https://cdn.example/image','thumbnailUrl':'','thumbnailRelativePath':''}
        receipt={'status':'UPLOADED_NOT_PUBLISHABLE','media_id':'m','source_sha256':'a'*64,'binding':{'response':response}}
        rule={'source_id':'cdc','source_sha256':'source','image_sha256':'a'*64,'valid_until_utc':'2099-01-01T00:00:00Z',
              'mode':'fixture','max_rms':1,'max_channel_delta':1,'max_aspect_error':1,'min_scale':1,'resample':'fixture'}
        with patch('source_mirror.official_scratch_publish.load_policy',return_value={'entries':[rule]}), \
             patch('source_mirror.official_scratch_publish.readback',return_value=b'output') as readback, \
             patch('source_mirror.official_scratch_publish.compare',return_value={'status':'VISUAL_QA_NOT_PUBLISHABLE'}):
            verify_media('/root',Path('/x/source/record'),'record',payload,{'m':b'original'},[receipt,receipt])
            self.assertEqual(readback.call_count,2)
            response['thumbnailRelativePath']='missing-url'
            with self.assertRaisesRegex(ValueError,'incomplete thumbnail'):verify_media('/root',Path('/x/source/record'),'record',payload,{'m':b'original'},[receipt,receipt])
