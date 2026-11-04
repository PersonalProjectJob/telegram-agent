import hashlib
from pathlib import Path
import tempfile
import unittest
from source_mirror.official_sources import SOURCES, collect
from source_mirror.official_media_candidates import discover


class CandidateTests(unittest.TestCase):
    def test_same_origin_candidates_only_and_no_rights_claim(self):
        html=(b'<html><head><title>Safety</title></head><body><nav><img src="/nav.png"></nav>'
              b'<main><p>Guidance</p><img src="/photo.png" alt="Workstation">'
              b'<img src="/photo.png" alt="duplicate"><img src="https://example.com/foreign.png">'
              b'<img src="/tracked.png?token=x"><aside><img src="/aside.png"></aside></main></body></html>')
        with tempfile.TemporaryDirectory() as d:
            collect(d,SOURCES[0],lambda _:html)
            result=discover(d,SOURCES[0]['id'],hashlib.sha256(html).hexdigest())
            self.assertEqual(result['candidates'],[{'url':'https://www.cdc.gov/photo.png','source_alt':'Workstation',
                                                    'status':'HOLD_RIGHTS_AND_CONTENT_UNVERIFIED'}])

    def test_corrupt_source_fails_closed(self):
        html=b'<html><head><title>Safety</title></head><body><main><p>Guidance</p></main></body></html>'
        with tempfile.TemporaryDirectory() as d:
            result=collect(d,SOURCES[0],lambda _:html)
            (Path(result['snapshot'])/'reference.txt').write_text('tampered')
            with self.assertRaises(ValueError): discover(d,SOURCES[0]['id'],hashlib.sha256(html).hexdigest())


if __name__=='__main__': unittest.main()
